from datetime import UTC, datetime, timedelta
from pathlib import Path

from kastellan.alarms import Alarms, Message, Severity, evaluate
from kastellan.config import Config, NotifyChannel
from kastellan.monitor import CheckStatus, DeviceStatus
from kastellan.notify import in_quiet_hours, wants

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

CONFIG = Config.model_validate(
    {
        "devices": {
            "router": {"host": "r"},
            "ap": {"host": "a", "parent": "router", "kind": "access-point"},
            "cam": {"host": "c", "parent": "ap"},
            "nas": {"host": "n", "parent": "router", "checks": ["ping", {"tcp": 22, "name": "ssh"}]},
            "laptop": {"host": "l", "kind": "laptop", "parent": "ap"},
        }
    }
)


def status(down: dict[str, list[str]], minutes: int = 10) -> dict[str, DeviceStatus]:
    """Every check up, except the listed ones, down for `minutes`."""
    out = {}
    for id, device in CONFIG.devices.items():
        out[id] = DeviceStatus(
            checks={
                c.name: CheckStatus(
                    up=c.name not in down.get(id, []),
                    since=NOW - timedelta(minutes=minutes),
                    detail="no reply" if c.name in down.get(id, []) else "",
                )
                for c in device.checks
            }
        )
    return out


def test_a_device_down_hides_what_is_behind_it() -> None:
    problems = evaluate(CONFIG, status({"ap": ["ping"], "cam": ["ping"], "laptop": ["ping"]}), NOW, {})
    assert [(p.key, p.severity) for p in problems] == [("down:ap", Severity.CRITICAL)]
    assert problems[0].hidden == ["cam"]  # the laptop may be off: never an alarm
    assert "and 1 device behind it" in problems[0].title


def test_not_before_down_after() -> None:
    assert evaluate(CONFIG, status({"router": ["ping"]}, minutes=1), NOW, {}) == []


def test_service_down_is_a_warning_and_mute_silences() -> None:
    problems = evaluate(CONFIG, status({"nas": ["ssh"]}), NOW, {})
    assert [(p.key, p.severity) for p in problems] == [("service:nas:ssh", Severity.WARNING)]
    assert evaluate(CONFIG, status({"nas": ["ssh"]}), NOW, {"nas": NOW + timedelta(hours=1)}) == []
    assert evaluate(CONFIG, status({"nas": ["ssh"]}), NOW, {"nas": NOW - timedelta(hours=1)}) != []


def test_open_resolve_and_events(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    down = evaluate(CONFIG, status({"router": ["ping"]}), NOW, {})
    [opened] = alarms.update(down, NOW)
    assert opened.severity is Severity.CRITICAL and not opened.resolved
    assert alarms.update(down, NOW + timedelta(minutes=1)) == []  # once
    [back] = alarms.update([], NOW + timedelta(minutes=20))
    assert back.resolved and back.title == "router is back up" and back.body == "after 30 min"
    assert Alarms(tmp_path).open == {}  # saved

    assert alarms.event("update:nas:jellyfin:10.11", "jellyfin 10.11", "", "nas", NOW) is not None
    assert alarms.event("update:nas:jellyfin:10.11", "jellyfin 10.11", "", "nas", NOW) is None
    assert "opened" in (tmp_path / "alarms.log").read_text()


def test_acknowledge(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    alarms.update(evaluate(CONFIG, status({"router": ["ping"]}), NOW, {}), NOW)
    alarm = alarms.acknowledge("down:router", "ada", "power cut, on it", NOW)
    assert alarm and alarm.acknowledged_by == "ada"
    assert Alarms(tmp_path).open["down:router"].acknowledged_note == "power cut, on it"


def test_quiet_hours_and_minimum() -> None:
    night = datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
    assert in_quiet_hours("22:00-07:00", night) and not in_quiet_hours("22:00-07:00", NOW)
    assert in_quiet_hours("12:00-13:00", NOW)
    channel = NotifyChannel(ntfy="https://ntfy.sh/x", min="warning", quiet="22:00-07:00")
    warning = Message(Severity.WARNING, "t", "")
    assert wants(channel, warning, NOW) and not wants(channel, warning, night)
    assert wants(channel, Message(Severity.CRITICAL, "t", ""), night)  # critical comes through
    assert not wants(channel, Message(Severity.INFO, "t", ""), NOW)
