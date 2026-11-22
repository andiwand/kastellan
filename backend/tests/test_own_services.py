"""A device's own services over time: kept per look, since when, and in the device's series."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from kastellan.monitor import DeviceStatus
from kastellan.own_services import ServiceStates

T0 = datetime(2026, 11, 22, 8, tzinfo=UTC)


def report(**running: bool) -> dict:
    services = [{"name": n, "running": r, "enabled": True} for n, r in running.items()]
    return {"services": [*services, {"name": "sysfixtime", "running": False, "enabled": False}]}


def test_since_when_and_after_a_restart(tmp_path: Path) -> None:
    s = ServiceStates(tmp_path)
    s.note("ap", report(dropbear=True, uhttpd=True), T0)
    s.note("ap", report(dropbear=True, uhttpd=False), T0 + timedelta(minutes=2))
    since = s.since("ap")
    assert set(since) == {"dropbear", "uhttpd"}  # sysfixtime: neither running nor at boot
    assert since["uhttpd"] == {"since": (T0 + timedelta(minutes=2)).isoformat(), "since_exact": True}
    assert since["dropbear"]["since_exact"] is False  # began with the first look or before

    again = ServiceStates(tmp_path)  # a restart: the history says it
    again.restore_from(T0 + timedelta(minutes=3))
    assert again.since("ap") == since
    again.note("ap", {}, T0 + timedelta(minutes=4))  # unreadable, not removed
    assert again.since("ap") == since
    again.note("ap", report(dropbear=True), T0 + timedelta(minutes=5))
    assert "uhttpd" not in again.since("ap")
    again.note("ap", report(dropbear=True, uhttpd=True), T0 + timedelta(minutes=6))
    assert again.since("ap")["uhttpd"] == {
        "since": (T0 + timedelta(minutes=6)).isoformat(),
        "since_exact": False,
    }
    again.forget(set())
    assert again.since("ap") == {}


def test_the_device_series(admin_client) -> None:
    c = admin_client("devices:\n  ap:\n    host: 192.0.2.1\n    openwrt: {}\n")
    hub = c.app.state.hub
    now = datetime.now(UTC)
    for k, up in enumerate([True, True, False, True]):
        hub.services.note("ap", report(uhttpd=up), now - timedelta(minutes=40 - 10 * k))
    hub.monitor.status.setdefault("ap", DeviceStatus())
    r = c.get("/api/series", params={"hours": 1, "buckets": 1, "device": "ap"}).json()
    assert r["services"]["uhttpd"][0]["uptime"] == 0.75
    assert "services" not in c.get("/api/series", params={"hours": 1}).json()  # the overview: checks only
    device = next(d for d in c.get("/api/devices").json() if d["id"] == "ap")
    assert device["services_since"]["uhttpd"]["since_exact"] is True
