"""Which network a device is in, and what is reachable from the internet.

- A device's network comes from its address: one of `networks:` in config.yaml whose `cidr`
  holds it, or else its private /24 (IPv6: /64), named after it. A public address is "internet".
  `network:` on a device says it outright, also several: a device can sit on the boundary
  between networks (a router: [home, internet]; a VPN server: [home, vpn]). The hub's own
  gateway is on the boundary to the internet by itself. Devices in one network are simply
  connected; several local networks (home, a holiday flat, the office) are separate zones.
- Ways in from the internet: checks whose target is a public name or address (`https://
  norn.example.org` is one way into SV1, whatever address it leads to), and devices marked
  `exposed`. That is what Kastellan knows; a port forward nobody wrote down stays unknown.

    networks:
      home:
        name: Home
        cidr: 192.168.15.0/24
      flat:
        name: Holiday flat
        cidr: 10.0.5.0/24
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .config import Check, Config, Device
from .tls import public_name

INTERNET = "internet"


@dataclass(frozen=True)
class Network:
    id: str
    name: str
    cidr: str | None
    local: bool

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "cidr": self.cidr, "local": self.local}


def _ip(text: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((text or "").strip("[]"))
    except ValueError:
        return None


# Addresses that only mean something on a local network; everything else is the internet.
LOCAL = [
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # private, CGNAT, Tailscale
        "127.0.0.0/8",
        "169.254.0.0/16",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
]


def _local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip.version == n.version and ip in n for n in LOCAL)


def is_public(address: str | None) -> bool:
    ip = _ip(address)
    return bool(ip and not _local(ip))


def _by_address(config: Config, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> Network:
    for id, n in config.networks.items():
        if n.cidr and ip in ipaddress.ip_network(n.cidr, strict=False):
            return Network(id, n.name or id, n.cidr, True)
    if not _local(ip):
        return THE_INTERNET
    prefix = 24 if ip.version == 4 else 64
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return Network(f"net-{net.network_address}", str(net), str(net), True)


THE_INTERNET = Network(INTERNET, "Internet", None, False)


@dataclass(frozen=True)
class Hub:
    """Where the hub is: its network (from its IPv4 address) and its IPv6 address, if any; a
    global IPv6 address in the same /64 as the hub's is at home, not on the internet."""

    network: Network | None = None
    v6: str | None = None


def network_of(config: Config, address: str | None, hub: Hub | None = None) -> Network | None:
    """The network an address is in, or None."""
    ip = _ip(address)
    if ip is None:
        return None
    if (
        ip.version == 6
        and hub
        and hub.network
        and hub.v6
        and not any(
            n.cidr and ip in ipaddress.ip_network(n.cidr, strict=False) for n in config.networks.values()
        )
    ):
        own = ipaddress.ip_network(f"{hub.v6}/64", strict=False)
        if ip in own:
            return hub.network
    return _by_address(config, ip)


def networks_of(
    config: Config, device: Device, address: str | None, gateway: str | None, hub: Hub | None = None
) -> list[Network]:
    """Every network a device is in: those config.yaml names for it, else the one of its address;
    and the internet for the hub's gateway (the router the way out goes through), which sits on
    the boundary between home and the internet."""
    out: list[Network] = []
    for id in device.network:
        if id == INTERNET:
            out.append(THE_INTERNET)
        else:
            n = config.networks[id]
            out.append(Network(id, n.name or id, n.cidr, True))
    if not device.network:
        own = network_of(config, address, hub)
        if own:
            out.append(own)
    if gateway and address and _ip(gateway) == _ip(address) and THE_INTERNET not in out:
        out.append(THE_INTERNET)
    return out


def public_ways(device: Device, on_internet: bool, statuses: dict[str, Any]) -> list[dict[str, Any]]:
    """The checks that reach this device from the internet's side: by a public name, or by an
    address on the internet. Checks of the device's own address count only when the device is on
    the internet (a home IPv6 address is global, yet no way in)."""
    out = []
    for check in device.checks:
        if check.type == "ping":
            continue  # not a service: no way in
        target = _target_host(device, check)
        if target == device.host:
            if not on_internet:
                continue
        elif not (public_name(target) or is_public(target)):
            continue
        status = statuses.get(check.name)
        out.append(
            {
                "check": check.name,
                "target": check.target if check.type == "http" else target,
                "address": getattr(status, "address", None) if status else None,
            }
        )
    return out


def _target_host(device: Device, check: Check) -> str:
    if check.type == "http" and isinstance(check.target, str):
        return urlsplit(check.target).hostname or ""
    return device.host


def default_gateway() -> str | None:
    """The hub's default gateway (Linux: /proc/net/route; with host networking that's the
    host's), or None."""
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    return str(ipaddress.IPv4Address(bytes.fromhex(parts[2])[::-1]))
    except (OSError, ValueError):
        pass
    return None


class Resolver:
    """Device names to addresses, looked up now and then (not on every page load)."""

    def __init__(self) -> None:
        self.addresses: dict[str, str | None] = {}

    async def refresh(self, hosts: set[str]) -> None:
        loop = asyncio.get_running_loop()

        async def one(host: str) -> tuple[str, str | None]:
            if _ip(host):
                return host, host.strip("[]")
            # IPv4 first: a home device's IPv4 address says which network it is in; its IPv6
            # one is often global. Any error (a name IDNA refuses, too slow) is just "unknown".
            for family in (socket.AF_INET, socket.AF_UNSPEC):
                try:
                    infos = await asyncio.wait_for(
                        loop.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM), 5
                    )
                except Exception:
                    continue
                if infos:
                    return host, str(infos[0][4][0])
            return host, None

        found = await asyncio.gather(*(one(h) for h in hosts))
        self.addresses = dict(found)

    def address(self, host: str) -> str | None:
        if _ip(host):
            return host.strip("[]")
        return self.addresses.get(host)
