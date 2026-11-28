from __future__ import annotations

from pathlib import Path

from kastellan import logs
from kastellan.logs import Line, LogStore


def test_logd_entries_after_the_cursor(tmp_path: Path) -> None:
    entries = [
        {
            "msg": "dropbear[812]: Password auth succeeded for 'root' from 192.168.1.5:5023",
            "priority": 86,
            "source": 1,
            "time": 1_700_000_000_000,
            "id": 1,
        },
        {
            "msg": "[   12.345678] br-lan: port 1(lan1) entered forwarding state",
            "priority": 6,
            "source": 0,
            "time": 1_700_000_000_000,
            "id": 2,
        },
        {
            "msg": "openvpn(server)[1200]: client1/1.2.3.4:5555 TLS Error: TLS handshake failed",
            "priority": 27,
            "source": 1,
            "time": 1_700_000_001_000,
            "id": 3,
        },
    ]
    lines, cursor = logs.from_logd("wifi1", entries, None)
    assert [(ln.program, ln.level, ln.source) for ln in lines] == [
        ("dropbear", 6, "syslog"),
        ("kernel", 6, "kernel"),
        ("openvpn(server)", 3, "syslog"),
    ]
    assert lines[0].msg.startswith("Password auth succeeded")
    assert lines[1].msg.startswith("br-lan")  # the kernel's uptime stamp goes
    assert cursor == {"t": 1_700_000_001_000, "ids": [3]}
    # The same buffer again: nothing new; one more line: only it.
    assert logs.from_logd("wifi1", entries, cursor) == ([], cursor)
    more = [*entries, {"msg": "x: y", "priority": 30, "source": 1, "time": 1_700_000_001_000, "id": 4}]
    lines, cursor = logs.from_logd("wifi1", more, cursor)
    assert [ln.msg for ln in lines] == ["y"] and cursor == {"t": 1_700_000_001_000, "ids": [3, 4]}
    # After a reboot the ids start again, the times go on.
    lines, _ = logs.from_logd(
        "wifi1", [{"msg": "a: b", "priority": 6, "source": 1, "time": 1_700_000_090_000, "id": 1}], cursor
    )
    assert len(lines) == 1


def test_logd_garbage_times_are_skipped() -> None:
    """logd once returned entries timed near 2**63 ms; as the cursor they hid every line after."""
    now = 1_800_000_000.0
    entries = [
        {"msg": "dropbear[1]: auth]", "priority": 1, "source": 1, "time": 8_910_533_468_664_195_000, "id": 7},
        {"msg": "00", "priority": 0, "source": 0, "time": -5, "id": 8},
        {"msg": "a: real", "priority": 6, "source": 1, "time": 1_799_999_999_000, "id": 9},
    ]
    lines, cursor = logs.from_logd("wifi1", entries, None, now=now)
    assert [ln.msg for ln in lines] == ["real"] and cursor == {"t": 1_799_999_999_000, "ids": [9]}


def test_store_newest_and_prune_skip_the_future(tmp_path: Path) -> None:
    store = LogStore(tmp_path / "logs.db")
    now = 1_800_000_000.0
    store.add(
        [
            Line(now - 60, "wifi1", "syslog", "a", 6, "real"),
            Line(now - 30, "wifi1", "fritzbox", "a", 6, "other source"),
            Line(8.9e15, "wifi1", "syslog", "", 1, "auth]"),
        ]
    )
    assert store.newest("wifi1", ("syslog", "kernel"), now=now) == now - 60
    assert store.newest("gw", ("syslog",), now=now) is None
    assert store.prune(14, 1000, now=now) == 1
    assert [r["msg"] for r in store.query()] == ["other source", "real"]


def test_store_filters_and_prunes(tmp_path: Path) -> None:
    store = LogStore(tmp_path / "logs.db")
    store.add(
        [
            Line(100, "wifi1", "syslog", "dropbear", 6, "Password auth succeeded for root"),
            Line(101, "wifi1", "syslog", "dropbear", 4, "Bad password attempt for root from 1.2.3.4"),
            Line(102, "gw", "fritzbox", "fritzbox", 6, "Internetverbindung wurde erfolgreich hergestellt"),
            Line(103, "sv1", "syslog", "sshd", 6, "50% done_ok", verified=False),
        ]
    )
    assert [r["ts"] for r in store.query()] == [103, 102, 101, 100]  # newest first
    assert [r["device"] for r in store.query(devices=["wifi1"])] == ["wifi1", "wifi1"]
    assert [r["level"] for r in store.query(max_level=4)] == [4]
    assert [r["ts"] for r in store.query(text="root")] == [101, 100]
    assert [r["ts"] for r in store.query(text="root 1.2.3.4")] == [101]  # every word
    assert [r["ts"] for r in store.query(text="50%")] == [103]  # % and _ are plain characters
    assert store.query(text="done_ok")[0]["verified"] is False
    assert [r["ts"] for r in store.query(since=101.5)] == [103, 102]
    assert [r["ts"] for r in store.matching(0, ["%"], limit=2)] == [102, 103]
    first = store.query(limit=2)
    assert [r["ts"] for r in store.query(before_id=first[-1]["id"])] == [101, 100]
    assert [r["ts"] for r in store.query(after_id=first[-1]["id"])] == [103]
    facets = store.facets(0)
    assert facets["devices"] == {"wifi1": 2, "gw": 1, "sv1": 1} and facets["total"] == 4
    store.set_state("openwrt:wifi1", {"t": 5, "ids": [1]})
    assert store.get_state("openwrt:wifi1") == {"t": 5, "ids": [1]}
    assert store.prune(days=1, max_lines=10, now=100 + 86400 + 1.5) == 2  # 100, 101 too old
    assert store.prune(days=365, max_lines=1, now=200) == 1
    assert [r["ts"] for r in store.query()] == [103]


def test_syslog_messages() -> None:
    line = logs.parse_syslog(
        b"<34>1 2026-11-21T10:00:00Z nas sshd 123 - - Failed password for x", "nas", now=5
    )
    assert line == Line(5, "nas", "syslog", "sshd", 2, "Failed password for x", False)
    line = logs.parse_syslog(b"<30>Nov 21 10:00:00 cam1 ntpd[44]: time synced", "cam1", now=5)
    assert line is not None and (line.program, line.level, line.msg) == ("ntpd", 6, "time synced")
    line = logs.parse_syslog(b"<13>hello there", "x", now=5)
    assert line is not None and (line.program, line.msg) == ("", "hello there")
    assert logs.parse_syslog(b"no priority", "x") is None


def test_syslog_only_from_known_devices(tmp_path: Path) -> None:
    receiver = logs.SyslogReceiver(lambda a: {"192.168.1.9": "nas"}.get(a))
    receiver.datagram_received(b"<30>sshd[1]: hi", ("192.168.1.9", 514))
    receiver.datagram_received(b"<30>sshd[1]: forged", ("10.0.0.66", 514))
    assert [ln.msg for ln in receiver.take()] == ["hi"]
    assert (receiver.received, receiver.refused) == (1, 1)


def test_follow_catches_up_without_skipping_and_ids_survive_pruning(tmp_path: Path) -> None:
    store = LogStore(tmp_path / "logs.db")
    store.add([Line(100 - i, "ap", "syslog", "x", 6, str(i)) for i in range(8)])
    cursor, seen = 0, set()
    while rows := store.query(after_id=cursor, limit=3):
        seen.update(r["msg"] for r in rows)
        cursor = max(r["id"] for r in rows)
    assert seen == {str(i) for i in range(8)}
    store.prune(1, 100, now=1000000)
    store.add([Line(1000001, "ap", "syslog", "x", 0, "emergency")])
    assert store.query(after_id=cursor)[0]["level"] == 0
    for path in tmp_path.glob("logs.db*"):
        assert path.stat().st_mode & 0o777 == 0o600
    store.close()


async def test_wireguard_presence_from_handshakes(tmp_path: Path, monkeypatch) -> None:
    import time
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from kastellan import logs_poll
    from kastellan.config import Config
    from kastellan.vpn import wireguard
    from kastellan.vpn.wireguard import WgClient

    recent = datetime.fromtimestamp(time.time() - 30, UTC).isoformat()
    handshakes = {"phone": recent}

    class Fake:
        def __init__(self, *a) -> None:
            pass

        async def clients(self):
            return [
                WgClient("0", "odd", True, "10.8.0.9", None, None, "not a time", None, None),
                WgClient("1", "phone", True, "10.8.0.2", None, None, handshakes["phone"], None, None),
            ]

    monkeypatch.setattr(wireguard, "WgEasy", Fake)
    config = Config.model_validate(
        {
            "devices": {"sv1": {"host": "sv1.lan"}},
            "vpns": {"wg": {"kind": "wireguard", "wg_easy": "http://sv1.lan:51821"}},
        }
    )
    hub = SimpleNamespace(
        config=config,
        logs=LogStore(tmp_path / "logs.db"),
        secrets=SimpleNamespace(get=lambda name: "pw"),
        vpn_addresses={"gone": {"10.9.0.2": "old"}},
        resolver=SimpleNamespace(address=lambda h: None),
    )
    await logs_poll.wireguard(hub)
    assert hub.vpn_addresses == {"wg": {"10.8.0.9": "odd (wg)", "10.8.0.2": "phone (wg)"}}
    await logs_poll.wireguard(hub)  # still there: nothing new
    handshakes["phone"] = datetime.fromtimestamp(time.time() - 600, UTC).isoformat()
    await logs_poll.wireguard(hub)
    lines = hub.logs.query()
    assert [(ln["device"], ln["source"], ln["msg"].split(" (")[0]) for ln in lines] == [
        ("sv1", "vpn", "phone went quiet"),
        ("sv1", "vpn", "phone connected"),
    ]


async def test_a_caught_up_router_is_asked_for_its_newest_lines(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from kastellan import logs_poll
    from kastellan.openwrt import client as ow

    buffer = [
        {"msg": f"daemon: line {i}", "priority": 30, "source": 1, "time": 1_000_000 + i, "id": i}
        for i in range(900)
    ]
    asked: list[int] = []

    async def read_log(ubus, lines: int = 1000):
        asked.append(lines)
        return buffer[-lines:]

    monkeypatch.setattr(ow, "read_log", read_log)
    hub = SimpleNamespace(logs=LogStore(tmp_path / "logs.db"), ubus=lambda id: None)
    assert await logs_poll.openwrt(hub, "r") == 900 and asked == [1000]  # first: all of it
    buffer.append({"msg": "daemon: one more", "priority": 30, "source": 1, "time": 2_000_000, "id": 900})
    assert await logs_poll.openwrt(hub, "r") == 1 and asked[-1] == 200  # then the newest
    for i in range(300):  # more than the newest 200 since: the whole buffer once more
        buffer.append(
            {"msg": f"daemon: burst {i}", "priority": 30, "source": 1, "time": 3_000_000 + i, "id": 1000 + i}
        )
    buffer[-2]["time"] = "malformed"  # does not count as overlap with the previous poll
    assert await logs_poll.openwrt(hub, "r") == 299 and asked[-2:] == [200, 1000]


async def test_a_cursor_in_the_future_goes_on_from_the_newest_real_line(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from kastellan import logs_poll
    from kastellan.openwrt import client as ow

    t0 = 1_700_000_000_000
    buffer = [
        {"msg": f"daemon: line {i}", "priority": 30, "source": 1, "time": t0 + i * 1000, "id": i}
        for i in range(5)
    ]

    async def read_log(ubus, lines: int = 1000):
        return buffer[-lines:]

    monkeypatch.setattr(ow, "read_log", read_log)
    hub = SimpleNamespace(logs=LogStore(tmp_path / "logs.db"), ubus=lambda id: None)
    # Stored before the fix: lines 0-2 and a garbage entry's time as the cursor.
    hub.logs.add([Line((t0 + i * 1000) / 1000, "r", "syslog", "daemon", 6, f"line {i}") for i in range(3)])
    hub.logs.set_state("openwrt:r", {"t": 8_910_533_468_664_195_000, "ids": [7]})
    assert await logs_poll.openwrt(hub, "r") == 2  # lines 3 and 4, nothing twice
    assert hub.logs.get_state("openwrt:r") == {"t": t0 + 4000, "ids": [4]}
