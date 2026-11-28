"""Every VPN server Kastellan can see on the network, managed or not: the OpenVPN servers and
WireGuard interfaces of the OpenWrt routers, a FRITZ!Box's WireGuard, and VPN containers on
the hosts (by their image). The VPN page lists them above the VPNs whose clients Kastellan
manages, with a link where one is such a VPN.

Read from what the hub already has (router reports, the `openvpn` ability's status, container
lists): nothing here asks a device anything."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config
    from .hub import Hub

# Images that are VPN servers (or their web front ends), by the last part of their name.
VPN_IMAGE = re.compile(
    r"(^|[/_-])(wg-easy|wireguard|wg-portal|wireguard-ui|openvpn(-as)?|pritunl|softether(vpn)?|"
    r"strongswan|ipsec-vpn-server|headscale|netbird|firezone|nebula|zerotier(-one)?|tailscale|"
    r"innernet|defguard)$",
    re.I,
)
STALE = 300  # an OpenVPN status file older than this (seconds): the server isn't writing it


def _kind_of_image(image: str) -> str:
    name = image.lower()
    for word, kind in (
        ("openvpn", "openvpn"),
        ("pritunl", "openvpn"),
        ("ipsec", "ipsec"),
        ("strongswan", "ipsec"),
        ("softether", "softether"),
        ("tailscale", "tailscale"),
        ("headscale", "tailscale"),
        ("zerotier", "zerotier"),
        ("nebula", "nebula"),
        ("netbird", "wireguard"),
    ):
        if word in name:
            return kind
    return "wireguard"


def _host_of(url: str) -> str | None:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def wg_easy_hosts(config: Config) -> frozenset[str]:
    """The names in the managed wg-easy addresses, for the hub to resolve with the devices'."""
    hosts = (_host_of(str(v.wg_easy)) for v in config.vpns.values() if v.kind == "wireguard")
    return frozenset(h for h in hosts if h)


def _same_host(hub: Hub, url_host: str | None, host: str, id: str) -> bool:
    """Whether wg-easy's address is this device: by name, or by the address both resolve to
    (wg-easy is often reached by a name of its own through a reverse proxy on the same host)."""
    if not url_host:
        return False
    if url_host in {host, id}:
        return True
    address = hub.resolver.address(url_host)
    return address is not None and address == hub.resolver.address(host)


def collect(hub: Hub, admin: bool) -> list[dict[str, Any]]:
    """The servers, by device. Client addresses only for admins (like the VPNs' own lists)."""
    out: list[dict[str, Any]] = []
    vpns = hub.config.vpns

    def managed_openvpn(device: str, name: str, count: int) -> str | None:
        for vid, v in vpns.items():
            if v.kind != "openvpn" or v.server != device:
                continue
            if v.instance == name or (v.instance is None and count == 1):
                return vid
        return None

    for device, state in sorted(hub.vpn_servers.items()):
        if device not in hub.config.devices:
            continue
        servers = state.get("servers") or {}
        if state.get("error") and not servers:
            out.append(
                {
                    "device": device,
                    "source": "openwrt",
                    "kind": "openvpn",
                    "name": None,
                    "error": state["error"] if admin else "can't be read right now",
                    "checked": state.get("checked"),
                }
            )
            continue
        for name, s in servers.items():
            age = s.get("age")
            running = s.get("running")
            if running is None and age is not None:  # a server from its own file: by its status file
                running = age <= STALE
            out.append(
                {
                    "device": device,
                    "source": "openwrt",
                    "kind": "openvpn",
                    "name": name,
                    "port": s.get("port"),
                    "proto": s.get("proto"),
                    "dev": s.get("dev"),
                    "running": running,
                    "stale": age is not None and age > STALE,
                    "clients": None
                    if running is False or (age is not None and age > STALE)
                    else [
                        {"name": c.get("name"), "since": c.get("since")}
                        | ({"address": c.get("address")} if admin else {})
                        for c in s.get("connected") or []
                    ],
                    "vpn": managed_openvpn(device, name, len(servers)),
                    "checked": state.get("checked"),
                }
            )

    for id, device in hub.config.devices.items():
        status = hub.monitor.status.get(id)
        if status is None:
            continue
        for i in ((status.router or {}).get("interfaces") or []) if status.router else []:
            if i.get("proto") == "wireguard":
                out.append(
                    {
                        "device": id,
                        "source": "openwrt",
                        "kind": "wireguard",
                        "name": i.get("name"),
                        "running": bool(i.get("up")),
                        "uptime": i.get("uptime"),
                        "addresses": i.get("ipv4") if admin else None,
                        "clients": None,  # peers: not read (the interface only)
                        "vpn": None,
                        "checked": status.router_checked.isoformat() if status.router_checked else None,
                    }
                )
        wg = (status.fritz or {}).get("wireguard") if status.fritz else None
        if isinstance(wg, dict) and (wg.get("server") or wg.get("connections")):
            out.append(
                {
                    "device": id,
                    "source": "fritzbox",
                    "kind": "wireguard",
                    "name": "WireGuard",
                    "port": wg.get("port"),
                    "running": True if wg.get("server") else None,
                    "clients": None,  # its connections are listed below the table
                    "vpn": None,
                    "error": wg.get("error") if admin else None,
                    "checked": None,
                }
            )
        for c in status.containers or []:
            image = str(c.get("image") or "")
            if not VPN_IMAGE.search(image.rsplit("/", 1)[-1].split(":")[0]):
                continue
            kind = _kind_of_image(image)
            vpn = next(
                (
                    vid
                    for vid, v in vpns.items()
                    if v.kind == "wireguard"
                    and "wg-easy" in image.lower()
                    and _same_host(hub, _host_of(str(v.wg_easy)), device.host, id)
                ),
                None,
            )
            out.append(
                {
                    "device": id,
                    "source": "container",
                    "kind": kind,
                    "name": c.get("name"),
                    "image": f"{image}:{c.get('tag')}" if c.get("tag") else image,
                    "running": c.get("state") == "running",
                    "state": c.get("state"),
                    "clients": None,
                    "vpn": vpn,
                    "checked": status.containers_checked.isoformat() if status.containers_checked else None,
                }
            )
    # A wg-easy VPN Kastellan manages, if no container of it was matched: its clients' server.
    linked = {s["vpn"] for s in out if s.get("vpn")}
    for vid, v in vpns.items():
        if v.kind == "wireguard" and vid not in linked:
            out.append(
                {
                    "device": None,
                    "source": "wg-easy",
                    "kind": "wireguard",
                    "name": hub.config.vpn_name(vid),
                    "where": str(v.wg_easy) if admin else None,
                    "running": None,
                    "clients": None,
                    "vpn": vid,
                    "checked": None,
                }
            )
    # The servers whose clients Kastellan manages come first.
    out.sort(key=lambda s: s.get("vpn") is None)
    return out
