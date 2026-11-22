from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from kastellan.alarms import RESOLVE_AFTER, Alarms, Message, Severity, evaluate
from kastellan.config import Config, NotifyChannel
from kastellan.monitor import CheckStatus, DeviceStatus
from kastellan.notify import in_quiet_hours, wants

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
STARTED = NOW - timedelta(hours=1)

CONFIG = Config.model_validate(
    {
        "devices": {
            "router": {"host": "r"},
            "ap": {"host": "a", "parent": "router", "kind": "access-point"},
            "cam": {"host": "c", "parent": "ap"},
            "nas": {
                "host": "n",
                "parent": "router",
                "checks": ["ping", {"http": "http://n/x/y", "name": "web/ui"}],
            },
            "laptop": {"host": "l", "kind": "laptop", "parent": "ap"},
        }
    }
)


def status(down: dict[str, list[str]], minutes: int = 10, checked: datetime = NOW) -> dict[str, DeviceStatus]:
    """Every check up, except the listed ones, down for `minutes`; all checked at `checked`."""
    out = {}
    for id, device in CONFIG.devices.items():
        out[id] = DeviceStatus(
            checks={
                c.name: CheckStatus(
                    up=c.name not in down.get(id, []),
                    since=NOW - timedelta(minutes=minutes),
                    detail="no reply" if c.name in down.get(id, []) else "",
                    checked=checked,
                )
                for c in device.checks
            }
        )
    return out


def problems(down: dict[str, list[str]], muted: dict | None = None, **kw):
    return evaluate(CONFIG, status(down, **kw), NOW, muted or {}, STARTED)


def test_the_top_most_device_down_covers_everything_behind_it() -> None:
    found = problems({"router": ["ping"], "ap": ["ping"], "cam": ["ping"], "laptop": ["ping"]})
    assert [(p.key, p.severity) for p in found] == [("down:router", Severity.CRITICAL)]
    assert found[0].hidden == ["ap", "cam"]  # the laptop may be off: never part of an alarm
    assert "and 2 devices behind it" in found[0].title


def test_not_before_down_after_and_not_from_old_results() -> None:
    assert problems({"router": ["ping"]}, minutes=1) == []
    # Results from before the hub started (history after a restart) don't raise alarms...
    assert problems({"router": ["ping"]}, checked=STARTED - timedelta(minutes=1)) == []
    # ...nor do results from a check that stopped reporting.
    assert problems({"router": ["ping"]}, checked=NOW - timedelta(hours=1)) == []


def test_a_service_down_alarms_as_its_check_says() -> None:
    found = problems({"nas": ["web/ui"]})
    assert [(p.key, p.severity) for p in found] == [("service:nas:web/ui", Severity.CRITICAL)]
    for alarm, expected in (("warning", [Severity.WARNING]), ("off", [])):
        config = CONFIG.model_copy(deep=True)
        config.devices["nas"].checks[1].alarm = alarm
        found = evaluate(config, status({"nas": ["web/ui"]}), NOW, {}, STARTED)
        assert [p.severity for p in found] == expected


def test_a_muted_device_still_covers_what_is_behind_it() -> None:
    muted = {"router": NOW + timedelta(hours=1)}
    assert problems({"router": ["ping"], "ap": ["ping"], "cam": ["ping"]}, muted) == []
    assert problems({"nas": ["web/ui"]}, {"nas": NOW - timedelta(minutes=1)}) != []  # mute ended


def test_children_wait_while_their_parent_settles() -> None:
    st = status({"cam": ["ping"]})
    st["ap"].checks["ping"].since = NOW - timedelta(seconds=20)  # ap just came back
    assert evaluate(CONFIG, st, NOW, {}, STARTED) == []


def test_open_hysteresis_resolve(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    down = problems({"router": ["ping"]})
    [opened] = alarms.update(down, NOW)
    assert opened.severity is Severity.CRITICAL and not opened.resolved
    assert alarms.update(down, NOW + timedelta(minutes=1)) == []  # once
    # Gone for a moment, then back: no "back up", no new alarm (flapping).
    assert alarms.update([], NOW + timedelta(minutes=2)) == []
    assert alarms.update(down, NOW + timedelta(minutes=2, seconds=30)) == []
    # Gone for good: resolved after RESOLVE_AFTER.
    assert alarms.update([], NOW + timedelta(minutes=3)) == []
    [back] = alarms.update([], NOW + timedelta(minutes=3, seconds=RESOLVE_AFTER))
    assert back.resolved and back.title == "router is back up"
    assert Alarms(tmp_path).open == {}  # saved


def test_folded_or_muted_alarms_end_silently(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    alarms.update(problems({"ap": ["ping"]}), NOW)  # ap down first
    later = NOW + timedelta(minutes=5)
    both = problems({"router": ["ping"], "ap": ["ping"]})
    messages = alarms.update(both, later)  # now the router: ap folds into its alarm
    assert [m.title for m in messages] == ["router is down, and 1 device behind it"]
    assert "down:ap" not in alarms.open
    assert alarms.update([], later + timedelta(minutes=10), quiet={"router"}) == []  # muted: silent
    assert alarms.open == {}


def test_events_once_and_quietly_at_start(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    assert alarms.event("update:nas:jf:10.11", "t", "", "nas", NOW, send=False) is None  # first look
    assert alarms.event("update:nas:jf:10.11", "t", "", "nas", NOW) is None  # already known
    assert alarms.event("update:nas:jf:10.12", "t", "", "nas", NOW) is not None
    assert "update:nas:jf:10.12" in Alarms(tmp_path).seen_events


def test_acknowledge(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    alarms.update(problems({"router": ["ping"]}), NOW)
    alarm = alarms.acknowledge("down:router", "ada", "power cut, on it", NOW)
    assert alarm and alarm.acknowledged_by == "ada"
    assert Alarms(tmp_path).open["down:router"].acknowledged_note == "power cut, on it"


def local(hour: int, minute: int = 0) -> datetime:
    """A time of day where the hub runs (quiet hours are local)."""
    return datetime(2026, 1, 15, hour, minute).astimezone()


def test_quiet_hours_are_local_and_validated() -> None:
    assert in_quiet_hours("22:00-07:00", local(23, 30)) and in_quiet_hours("22:00-07:00", local(6))
    assert not in_quiet_hours("22:00-07:00", local(12))
    channel = NotifyChannel(ntfy="https://ntfy.sh/x", min="warning", quiet="22:00-07:00")
    warning = Message(Severity.WARNING, "t", "")
    assert wants(channel, warning, local(12)) and not wants(channel, warning, local(23))
    assert wants(channel, Message(Severity.CRITICAL, "t", ""), local(23))  # critical comes through
    assert not wants(channel, Message(Severity.INFO, "t", ""), local(12))
    with pytest.raises(ValidationError):  # used to pass, and then dropped every notification
        NotifyChannel(ntfy="https://ntfy.sh/x", quiet="25:99-07:00")


def test_alarms_say_where_a_device_away_from_home_is() -> None:
    config = Config.model_validate(
        {
            "sites": {"hetzner": "Hetzner Cloud"},
            "devices": {"nas": {"host": "n", "name": "NAS"}, "vps": {"host": "v", "site": "hetzner"}},
        }
    )
    down = CheckStatus(up=False, since=NOW - timedelta(minutes=10), checked=NOW)
    st = {id: DeviceStatus(checks={"ping": down}) for id in config.devices}
    titles = sorted(p.title for p in evaluate(config, st, NOW, {}, STARTED))
    assert titles == ["NAS is down", "vps (Hetzner Cloud) is down"]


def test_a_virtual_machine_folds_into_its_host_and_says_why_it_is_down() -> None:
    """A device that runs on another as a VM (runs_on): the host down covers it, like a parent;
    with the host up and the VM shut off, its own alarm says so."""
    config = Config.model_validate(
        {
            "devices": {
                "router": {"host": "r"},
                "sv1": {"host": "s", "parent": "router", "kind": "server"},
                "ha": {"host": "h", "parent": "router", "runs_on": {"host": "sv1", "vm": "haos"}},
            }
        }
    )
    st = {
        id: DeviceStatus(
            checks={
                "ping": CheckStatus(
                    up=id not in ("sv1", "ha"), since=NOW - timedelta(minutes=10), checked=NOW
                )
            }
        )
        for id in config.devices
    }
    found = evaluate(config, st, NOW, {}, STARTED)
    assert [p.key for p in found] == ["down:sv1"] and found[0].hidden == ["ha"]
    st["router"].checks["ping"].up = False
    found = evaluate(config, st, NOW, {}, STARTED)
    assert [p.key for p in found] == ["down:router"] and found[0].hidden == ["ha", "sv1"]
    st["router"].checks["ping"].up = True
    st["sv1"].checks["ping"] = CheckStatus(up=True, since=NOW - timedelta(minutes=10), checked=NOW)
    st["sv1"].host = {"vms": [{"name": "haos", "state": "shut off"}]}
    st["sv1"].host_checked = NOW  # read just now: it says what is so now
    found = evaluate(config, st, NOW, {}, STARTED)
    assert [p.key for p in found] == ["down:ha"]
    assert found[0].detail == "its virtual machine on sv1 is shut off"


def test_a_virtual_router_and_its_host_do_not_hide_each_others_alarm() -> None:
    config = Config.model_validate(
        {
            "devices": {
                "router": {"host": "r", "runs_on": {"host": "server", "vm": "router"}},
                "server": {"host": "s", "parent": "router"},
                "guest": {"host": "g", "parent": "router"},
            }
        }
    )
    st = {
        id: DeviceStatus(
            checks={
                "ping": CheckStatus(
                    up=False,
                    since=NOW - timedelta(minutes=10),
                    checked=NOW,
                )
            }
        )
        for id in config.devices
    }
    found = evaluate(config, st, NOW, {}, STARTED)
    assert [p.key for p in found] == ["down:router"]
    assert found[0].hidden == ["guest", "server"]


def test_mutes_that_ended_are_forgotten(tmp_path: Path) -> None:
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    alarms = Alarms(tmp_path)
    alarms.mute("a", now + timedelta(minutes=5))
    alarms.mute("b", now - timedelta(minutes=5))
    assert alarms.muted_now(now) == {"a": now + timedelta(minutes=5)}
    assert set(Alarms(tmp_path).muted) == {"a"}  # saved without the ended one
