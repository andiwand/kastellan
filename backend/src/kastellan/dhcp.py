"""DHCP: asking the network for an address like a new device would, to see which DHCP servers
answer (the `dhcp` check), and what the routers say about their address pools (openwrt/,
fritzbox/) and the devices that got an address.

The probe sends one DHCPDISCOVER (broadcast, from UDP port 68) and collects the offers for a
few seconds; it never asks for the offered address (no DHCPREQUEST), so no lease is taken. It
uses a fixed, locally administered hardware address of its own, so a server that holds an
offered address for a moment holds one address for Kastellan, not one per check. The hub must
be on the network it asks (host networking in Docker) and may bind port 68 (root).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any

COOKIE = b"\x63\x82\x53\x63"
MAC = bytes.fromhex("02") + bytes.fromhex("4b4153") + b"\x74\x6c"  # 02:4b:41:53:74:6c, "KAStl"
_lock = asyncio.Lock()  # one probe at a time: they share port 68
_sample: tuple[float, str, list[Offer]] | None = None
UNEXPECTED_FOR = 1800


def discover(xid: int, mac: bytes = MAC) -> bytes:
    """A DHCPDISCOVER asking for the answer by broadcast (Kastellan has no address to send to)."""
    head = struct.pack(
        "!BBBBIHH4s4s4s4s16s64s128s",
        1,
        1,
        6,
        0,
        xid,
        0,
        0x8000,  # BOOTREQUEST, Ethernet, broadcast flag
        b"\0" * 4,
        b"\0" * 4,
        b"\0" * 4,
        b"\0" * 4,
        mac.ljust(16, b"\0"),
        b"\0" * 64,
        b"\0" * 128,
    )
    options = bytes([53, 1, 1])  # DHCPDISCOVER
    options += bytes([55, 4, 1, 3, 6, 51])  # ask for: subnet mask, router, DNS, lease time
    options += bytes([12, 9]) + b"kastellan"  # host name
    return head + COOKIE + options + bytes([255])


@dataclass
class Offer:
    server: str  # option 54, or the sender
    offered: str
    router: str | None = None
    dns: list[str] = field(default_factory=list)
    lease: int | None = None
    latency_ms: float | None = None


def parse(data: bytes, xid: int, sender: str) -> Offer | None:
    """A DHCPOFFER for our request, or None."""
    if len(data) < 240 or data[0] != 2 or data[236:240] != COOKIE:
        return None
    if struct.unpack("!I", data[4:8])[0] != xid or data[1:3] != b"\x01\x06" or data[28:34] != MAC:
        return None
    offered = socket.inet_ntoa(data[16:20])
    opts: dict[int, bytes] = {}
    i = 240
    while i < len(data):
        code = data[i]
        if code == 255:
            break
        if code == 0:
            i += 1
            continue
        if i + 1 >= len(data):
            return None
        length = data[i + 1]
        if i + 2 + length > len(data):
            return None
        opts[code] = data[i + 2 : i + 2 + length]
        i += 2 + length
    if opts.get(53) != b"\x02":  # DHCPOFFER
        return None

    def addresses(raw: bytes | None) -> list[str]:
        raw = raw or b""
        return [socket.inet_ntoa(raw[j : j + 4]) for j in range(0, len(raw) - 3, 4)]

    server = (addresses(opts.get(54)) or [sender])[0]
    return Offer(
        server=server,
        offered=offered,
        router=(addresses(opts.get(3)) or [None])[0],
        dns=addresses(opts.get(6)),
        lease=struct.unpack("!I", opts[51])[0] if len(opts.get(51, b"")) == 4 else None,
    )


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self, xid: int) -> None:
        self.xid = xid
        self.started = time.monotonic()
        self.offers: dict[str, Offer] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr[1] != 67:
            return
        offer = parse(data, self.xid, addr[0])
        if offer is not None:
            offer.latency_ms = round((time.monotonic() - self.started) * 1000, 1)
            self.offers.setdefault(offer.server, offer)


async def probe(wait: float = 3.0, interface_address: str = "0.0.0.0") -> list[Offer]:
    """Every DHCP server's offer within `wait` seconds. PermissionError when port 68 can't be
    bound (not root)."""
    global _sample
    started = time.monotonic()
    async with _lock:
        if _sample and _sample[0] >= started and _sample[1] == interface_address:
            return _sample[2]  # concurrent checks share the broadcast they waited for
        xid = struct.unpack("!I", os.urandom(4))[0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((interface_address, 68))
            sock.setblocking(False)
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(lambda: _Receiver(xid), sock=sock)
        except BaseException:
            sock.close()
            raise
        try:
            transport.sendto(discover(xid), ("255.255.255.255", 67))
            await asyncio.sleep(wait)
            offers = list(protocol.offers.values())
            _sample = (time.monotonic(), interface_address, offers)
            return offers
        finally:
            transport.close()


# --- What the hub keeps: servers seen, addresses handed out ------------------------------------

UNEXPECTED: dict[str, dict[str, Any]] = {}  # server address -> {"device", "seen", "offered"}


def note_unexpected(device: str, offers: list[Offer], expected: set[str]) -> None:
    now = time.time()
    for o in offers:
        if o.server not in expected:
            UNEXPECTED[o.server] = {"device": device, "seen": now, "offered": o.offered}
    for server in list(UNEXPECTED):
        if now - UNEXPECTED[server]["seen"] > UNEXPECTED_FOR:
            del UNEXPECTED[server]


def in_range(address: str, low: str, high: str) -> bool:
    with contextlib.suppress(ValueError, TypeError):
        a = ipaddress.ip_address(address)
        return ipaddress.ip_address(low) <= a <= ipaddress.ip_address(high)
    return False
