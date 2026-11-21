from __future__ import annotations

import socket
import struct

from kastellan import dhcp
from kastellan.config import Check, CheckType


def offer(xid: int, server: str, yiaddr: str, kind: int = 2) -> bytes:
    head = bytearray(dhcp.discover(xid))[:240]
    head[0] = 2  # BOOTREPLY
    head[16:20] = socket.inet_aton(yiaddr)
    opts = bytes([53, 1, kind, 54, 4]) + socket.inet_aton(server)
    opts += (
        bytes([3, 4])
        + socket.inet_aton(server)
        + bytes([6, 8])
        + socket.inet_aton("1.1.1.1")
        + socket.inet_aton("9.9.9.9")
    )
    opts += bytes([51, 4]) + struct.pack("!I", 43200) + bytes([255])
    return bytes(head) + opts


def test_discover_asks_for_a_broadcast_answer() -> None:
    msg = dhcp.discover(0x12345678)
    assert msg[0] == 1 and struct.unpack("!I", msg[4:8])[0] == 0x12345678
    assert struct.unpack("!H", msg[10:12])[0] & 0x8000  # broadcast flag
    assert msg[28:34] == dhcp.MAC and msg[236:240] == dhcp.COOKIE
    assert bytes([53, 1, 1]) in msg[240:]


def test_offers_are_read_and_others_ignored() -> None:
    o = dhcp.parse(offer(7, "192.168.15.1", "192.168.15.200"), 7, "192.168.15.1")
    assert o == dhcp.Offer("192.168.15.1", "192.168.15.200", "192.168.15.1", ["1.1.1.1", "9.9.9.9"], 43200)
    assert dhcp.parse(offer(8, "192.168.15.1", "192.168.15.200"), 7, "x") is None  # someone else's
    assert dhcp.parse(offer(7, "192.168.15.1", "192.168.15.200", kind=5), 7, "x") is None  # an ACK
    assert dhcp.parse(b"short", 7, "x") is None


def test_an_unexpected_server_is_noted() -> None:
    dhcp.UNEXPECTED.clear()
    offers = [dhcp.Offer("192.168.15.1", "192.168.15.200"), dhcp.Offer("192.168.15.77", "10.0.0.5")]
    dhcp.note_unexpected("192.168.15.1", offers, {"192.168.15.1"})
    assert list(dhcp.UNEXPECTED) == ["192.168.15.77"]
    dhcp.UNEXPECTED.clear()


def test_the_check_in_config() -> None:
    c = Check.model_validate("dhcp")
    assert (c.type, c.name, c.target) == (CheckType.DHCP, "dhcp", None)
    try:
        Check.model_validate({"dhcp": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("dhcp takes no target")
