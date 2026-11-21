from __future__ import annotations

import socket
import struct
import time

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


def test_malformed_and_other_clients_offers_are_ignored() -> None:
    message = bytearray(offer(7, "192.168.1.1", "192.168.1.10"))
    message[28] ^= 1
    assert dhcp.parse(bytes(message), 7, "192.168.1.1") is None
    assert dhcp.parse(offer(7, "192.168.1.1", "192.168.1.10")[:-2], 7, "192.168.1.1") is None
    assert not dhcp.in_range("::1", "192.168.1.1", "192.168.1.200")


async def test_concurrent_checks_share_one_broadcast(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    sent = []

    class Socket:
        def __init__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, *args):
            pass

        def setblocking(self, *args):
            pass

        def close(self):
            pass

    async def endpoint(factory, sock):
        receiver = factory()

        def send(data, addr):
            sent.append(data)
            receiver.datagram_received(
                offer(receiver.xid, "192.168.1.1", "192.168.1.10"), ("192.168.1.1", 67)
            )

        return SimpleNamespace(sendto=send, close=lambda: None), receiver

    monkeypatch.setattr(dhcp, "socket", SimpleNamespace(**{**vars(socket), "socket": Socket}))
    monkeypatch.setattr(dhcp, "_lock", asyncio.Lock())
    monkeypatch.setattr(dhcp, "_sample", None)
    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", endpoint)
    results = await asyncio.gather(*(dhcp.probe(wait=0.01) for _ in range(4)))
    assert len(sent) == 1 and all(r == results[0] and r for r in results)


def test_alarms_expire_and_allow_configured_dhcp_servers(tmp_path, monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta

    from kastellan.config import Config
    from kastellan.hub import Hub
    from kastellan.monitor import DeviceStatus
    from kastellan.settings import Settings

    hub = Hub(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    hub.config = Config.model_validate(
        {
            "devices": {
                "one": {"host": "192.168.1.1", "checks": ["dhcp"]},
                "two": {"host": "192.168.1.2", "checks": ["dhcp"]},
            }
        }
    )
    now = time.time()
    monkeypatch.setattr(
        dhcp,
        "UNEXPECTED",
        {
            server: {"device": "192.168.1.1", "seen": now - age, "offered": "192.168.1.10"}
            for server, age in [("192.168.1.2", 0), ("192.168.1.3", 1900), ("192.168.1.4", 0)]
        },
    )
    assert [p.key for p in hub._dhcp_problems()] == ["dhcp:rogue:192.168.1.4"]
    hub.config.devices["one"].checks = []
    assert hub._dhcp_problems() == []
    status = DeviceStatus(router={"dhcp": {"size": 10, "used": 9}}, router_checked=datetime.now(UTC))
    hub.monitor.status["one"] = status
    assert [p.key for p in hub._dhcp_problems()] == ["dhcp:pool:one"]
    status.router_checked -= timedelta(hours=1)
    assert hub._dhcp_problems() == []

    # Existing leases stay remembered when unrelated events prune the old history.
    status.router["dhcp"]["leases"] = [{"mac": "02:11:22:33:44:55", "address": "192.168.1.10"}]
    event = "newdevice:02:11:22:33:44:55"
    hub.alarms.seen_events[event] = "2000-01-01T00:00:00+00:00"
    now = datetime.now(UTC)
    assert hub._events(now) == []
    assert hub.alarms.seen_events[event] == now.isoformat(timespec="seconds")


def test_dhcp_is_compact_in_yaml_and_never_an_internet_entry() -> None:
    from kastellan.config import Device
    from kastellan.config_edit import _check_node
    from kastellan.networks import public_ways

    assert _check_node({"type": "dhcp", "name": "dhcp"}) == "dhcp"
    assert public_ways(Device(host="1.2.3.4", checks=[Check.model_validate("dhcp")]), True, {}) == []


async def test_dhcp_uses_its_timeout_and_reports_reply_latency(monkeypatch) -> None:
    from kastellan import checks

    async def probe(wait):
        assert wait == 0.5
        return [dhcp.Offer("127.0.0.1", "127.0.0.2", latency_ms=8.5)]

    monkeypatch.setattr(dhcp, "probe", probe)
    result = await checks._dhcp("127.0.0.1", Check.model_validate({"dhcp": None, "timeout": 0.5}))
    assert result.up and result.latency_ms == 8.5
