"""Maintenance: which windows are on, and alarms held while they are (sent if still there after)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from kastellan import maintenance
from kastellan.alarms import RESOLVE_AFTER, Alarms, Problem, Severity
from kastellan.config import Config

DEVICES = {
    "nas": {"host": "10.0.0.2"},
    "pi": {"host": "10.0.0.3"},
    "vps": {"host": "203.0.113.5", "site": "hetzner"},
}


def config(*windows: dict) -> Config:
    return Config.model_validate(
        {"devices": DEVICES, "groups": {"servers": {"devices": ["nas", "pi"]}}, "maintenance": list(windows)}
    )


def local(*args: int) -> datetime:
    return datetime(*args).astimezone()  # type: ignore[misc]


def test_windows_cover_devices_groups_and_sites() -> None:
    c = config(
        {"groups": ["servers"], "sites": ["hetzner"], "start": "2026-11-28 20:00", "end": "2026-11-28 23:00"}
    )
    assert maintenance.covered(c, c.maintenance[0]) == ["nas", "pi", "vps"]
    assert set(maintenance.active(c, local(2026, 11, 28, 21), {})) == {"nas", "pi", "vps"}
    assert maintenance.active(c, local(2026, 11, 28, 23), {}) == {}  # the end is outside
    assert maintenance.active(c, local(2026, 11, 28, 19, 59), {}) == {}


def test_weekly_windows_across_midnight() -> None:
    # 2026-11-29 is a Sunday: 23:30 for 90 minutes runs into Monday.
    c = config({"name": "Patch night", "devices": ["nas"], "days": ["sun"], "at": "23:30", "minutes": 90})
    on = maintenance.active(c, local(2026, 11, 30, 0, 30), {})
    assert on["nas"].until == local(2026, 11, 30, 1, 0) and on["nas"].reason == "Patch night"
    assert maintenance.active(c, local(2026, 11, 30, 1, 0), {}) == {}
    assert maintenance.active(c, local(2026, 11, 29, 23, 0), {}) == {}
    [w] = maintenance.upcoming(c, local(2026, 11, 30, 2, 0))
    assert w["start"] == local(2026, 12, 6, 23, 30) and not w["now"]


def test_from_the_app_and_the_latest_end_wins() -> None:
    c = config({"devices": ["nas"], "start": "2026-11-28 20:00", "end": "2026-11-28 21:00"})
    now = local(2026, 11, 28, 20, 30)
    adhoc = {"nas": {"until": (now + timedelta(hours=2)).isoformat(), "reason": "disk swap"}}
    a = maintenance.active(c, now, adhoc)["nas"]
    assert a.reason == "disk swap" and not a.planned
    gone = {"pi": {"until": (now - timedelta(minutes=1)).isoformat(), "reason": ""}}
    assert "pi" not in maintenance.active(c, now, gone)


@pytest.mark.parametrize(
    "window",
    [
        {"devices": ["nas"]},  # no time
        {"devices": ["nas"], "days": ["sun"], "at": "03:00"},  # no minutes
        {"devices": ["nas"], "start": "2026-11-28 20:00", "end": "2026-11-28 19:00"},
        {"devices": ["nas"], "start": "2026-11-28 20:00", "end": "2026-11-28 21:00", "days": ["sun"]},
        {"start": "2026-11-28 20:00", "end": "2026-11-28 21:00"},  # for nothing
        {"devices": ["nope"], "start": "2026-11-28 20:00", "end": "2026-11-28 21:00"},
        {"groups": ["nope"], "days": ["sun"], "at": "03:00", "minutes": 5},
        {"devices": ["nas"], "days": ["sunday"], "at": "03:00", "minutes": 5},
    ],
)
def test_bad_windows_are_refused(window: dict) -> None:
    with pytest.raises(ValidationError):
        config(window)


NOW = datetime(2026, 11, 28, 20, 0, tzinfo=UTC)
DOWN = [Problem("down:nas", Severity.CRITICAL, "nas", "nas", "nas is down")]


def test_alarms_are_held_and_sent_only_if_still_there(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    assert alarms.update(DOWN, NOW, held={"nas"}) == []  # opened, not sent
    assert alarms.open["down:nas"].held
    assert alarms.update(DOWN, NOW + timedelta(minutes=5), held={"nas"}) == []
    [late] = alarms.update(DOWN, NOW + timedelta(minutes=10))  # over, still down: now it's news
    assert late.title == "nas is down" and "after maintenance" in late.body
    assert not alarms.open["down:nas"].held

    # Down and back up within the maintenance: never heard of.
    quiet = Alarms(tmp_path / "other")
    quiet.update(DOWN, NOW, held={"nas"})
    assert quiet.update([], NOW + timedelta(minutes=1), held={"nas"}) == []
    assert quiet.open == {}
    assert quiet.update([], NOW + timedelta(seconds=RESOLVE_AFTER * 3)) == []


def test_maintenance_from_the_app_runs_out(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    alarms.start_maintenance("nas", NOW + timedelta(hours=1), "disk swap", "ada")
    assert Alarms(tmp_path).maintenance["nas"]["reason"] == "disk swap"  # saved
    assert alarms.ended_maintenance(NOW) == []
    assert alarms.ended_maintenance(NOW + timedelta(hours=1)) == ["nas"]
    assert alarms.maintenance == {}


def test_the_routes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    path = tmp_path / "config.yaml"
    path.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n  pi:\n    host: 127.0.0.2\n"
        "groups:\n  servers:\n    devices: [nas, pi]\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('admin password')}\n"
    )
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        c.post("/api/auth/login", json={"user": "ada", "password": "admin password"}, headers=h)
        r = c.post("/api/groups/servers/maintenance", json={"minutes": 60, "reason": "patching"}, headers=h)
        assert r.status_code == 200 and r.json()["devices"] == ["nas", "pi"]
        on = c.get("/api/alarms").json()["maintenance"]
        assert on["pi"]["reason"] == "patching" and not on["pi"]["planned"]
        assert c.post("/api/devices/pi/maintenance", json={"minutes": 0}, headers=h).status_code == 200
        assert set(c.get("/api/alarms").json()["maintenance"]) == {"nas"}
        assert c.post("/api/devices/nope/maintenance", json={"minutes": 5}, headers=h).status_code == 404

        version = c.get("/api/settings").json()["version"]
        window = {"name": "Patch night", "groups": ["servers"], "days": ["sun"], "at": "03:00", "minutes": 90}
        r = c.put(
            "/api/settings/maintenance",
            json={"value": {"maintenance": [window]}, "version": version},
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert "at: 03:00" in path.read_text() or "at: '03:00'" in path.read_text()
        [w] = c.get("/api/alarms").json()["windows"]
        assert w["devices"] == ["nas", "pi"] and w["start"]
        bad = {
            "value": {"maintenance": [{"devices": ["nas"]}]},
            "version": c.get("/api/settings").json()["version"],
        }
        assert c.put("/api/settings/maintenance", json=bad, headers=h).status_code == 400


def test_reminders_until_acknowledged(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    every = timedelta(hours=4)
    [_] = alarms.update(DOWN, NOW, remind=every)
    assert alarms.update(DOWN, NOW + timedelta(hours=3), remind=every) == []
    [again] = alarms.update(DOWN, NOW + timedelta(hours=4), remind=every)
    assert again.title == "Still: nas is down" and again.body.startswith("for 4 h, not acknowledged")
    assert alarms.update(DOWN, NOW + timedelta(hours=5), remind=every) == []  # counts from then
    assert alarms.update(DOWN, NOW + timedelta(hours=8), remind=every, quiet={"nas"}) == []  # muted
    alarms.acknowledge("down:nas", "ada", "", NOW + timedelta(hours=8))
    assert alarms.update(DOWN, NOW + timedelta(hours=20), remind=every) == []
    assert alarms.update(DOWN, NOW + timedelta(hours=40)) == []  # off


def test_a_released_alarm_reminds_from_its_release(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    every = timedelta(hours=1)
    alarms.update(DOWN, NOW, held={"nas"}, remind=every)
    assert alarms.update(DOWN, NOW + timedelta(hours=2), held={"nas"}, remind=every) == []
    [late] = alarms.update(DOWN, NOW + timedelta(hours=3), remind=every)
    assert "after maintenance" in late.body
    assert alarms.update(DOWN, NOW + timedelta(hours=3, minutes=30), remind=every) == []


def test_existing_alarms_are_held_when_maintenance_starts(tmp_path: Path) -> None:
    alarms = Alarms(tmp_path)
    warning = [Problem("down:nas", Severity.WARNING, "nas", "nas", "nas is down")]
    assert alarms.update(warning, NOW)
    assert alarms.update(DOWN, NOW + timedelta(minutes=1), held={"nas"}) == []
    assert Alarms(tmp_path).open["down:nas"].held
    assert alarms.update([], NOW + timedelta(minutes=2), held={"nas"}) == []
    assert alarms.open == {}
    # It may recover on the very first evaluation inside the window, too.
    alarms.update(DOWN, NOW)
    assert alarms.update([], NOW + timedelta(minutes=1), held={"nas"}) == []
    assert alarms.open == {}


def test_overlapping_weekly_occurrences_end_at_the_latest_end() -> None:
    c = config({"devices": ["nas"], "days": ["sun", "mon"], "at": "00:00", "minutes": 2880})
    now = local(2026, 11, 30, 12)
    assert maintenance.active(c, now, {})["nas"].until == local(2026, 12, 2, 0)


async def test_maintenance_suppresses_notifications_already_waiting_to_retry(
    tmp_path: Path, monkeypatch
) -> None:
    from kastellan import notify
    from kastellan.alarms import Message
    from kastellan.config import NotifyChannel
    from kastellan.hub import Hub
    from kastellan.push import Push

    hub = object.__new__(Hub)
    hub.config = config()
    channel = NotifyChannel.model_validate({"ntfy": "https://example.test/topic"})
    hub.config.notify = [channel]
    hub.alarms = Alarms(tmp_path)
    hub.alarms.start_maintenance("nas", NOW + timedelta(hours=1), "patching", "ada")
    hub.push = Push(tmp_path)
    message = Message(Severity.CRITICAL, "nas is down", "", "nas")
    hub._outbox = [(channel, message, NOW - timedelta(minutes=5), NOW - timedelta(minutes=5), 1)]
    sent = []

    async def send(channels, messages, now, **kwargs):
        sent.extend(messages)
        return []

    monkeypatch.setattr(notify, "send", send)
    await hub._send([message], NOW)
    await hub._retry_outbox(NOW)
    assert sent == [] and hub._outbox == []
