"""Disk usage over time: when a filesystem will be full, and the alarm for it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from kastellan.alarms import host_problems
from kastellan.config import AlarmSettings
from kastellan.disks import DiskHistory, forecast

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
GB = 10**9


def filling(days: int, per_day: float, total: int = 100 * GB, start: float = 50 * GB) -> list:
    """Hourly samples up to NOW, filling `per_day` bytes a day."""
    out = []
    for h in range(days * 24, -1, -1):
        used = int(start + per_day * (days * 24 - h) / 24)
        out.append((NOW - timedelta(hours=h), used, total - used))
    return out


def test_days_left_at_the_rate_it_fills() -> None:
    days = forecast(filling(5, 2 * GB), NOW)  # 40 GB left at 2 GB a day
    assert days is not None and abs(days - 20) < 0.1
    assert forecast(filling(5, 0), NOW) is None  # steady
    assert forecast(filling(5, -GB), NOW) is None  # emptying
    assert forecast(filling(5, 1000), NOW) is None  # years away
    assert forecast(filling(5, 2 * GB)[-10:], NOW) is None  # under a day of history


def test_a_clean_up_starts_afresh() -> None:
    before = filling(5, 5 * GB)
    after = [(t, u - 30 * GB, a + 30 * GB) for t, u, a in filling(2, 0)]
    # Cleaned up 2 days ago, steady since: no forecast from the fast filling before.
    samples = [s for s in before if s[0] < NOW - timedelta(days=2)] + after
    assert forecast(samples, NOW) is None


def test_hourly_samples_kept_and_forgotten(tmp_path: Path) -> None:
    disks = DiskHistory(tmp_path)
    report = lambda used: {"filesystems": [{"mount": "/", "used": used, "available": 100 * GB - used}]}  # noqa: E731
    for h in range(0, 48 * 60, 10):  # every 10 minutes for two days, 1 GB an hour
        disks.note("nas", report(10 * GB + h * GB // 60), NOW + timedelta(minutes=h))
    assert len(disks.data["nas"]["/"]) == 48
    days = DiskHistory(tmp_path).forecasts("nas", NOW + timedelta(hours=48))["/"]  # saved
    assert 1 < days < 3
    disks.forget({"pi"})
    assert DiskHistory(tmp_path).data == {}


def test_a_mount_that_went_is_forgotten_in_time(tmp_path: Path) -> None:
    disks = DiskHistory(tmp_path)
    fs = lambda mount: {"mount": mount, "used": GB, "available": GB}  # noqa: E731
    disks.note("nas", {"filesystems": [fs("/"), fs("/mnt/usb")]}, NOW)
    disks.note("nas", {"filesystems": [fs("/")]}, NOW + timedelta(days=29))
    assert set(disks.data["nas"]) == {"/", "/mnt/usb"}  # its history still counts
    disks.note("nas", {"filesystems": [fs("/")]}, NOW + timedelta(days=31))
    assert set(DiskHistory(tmp_path).data["nas"]) == {"/"}


def test_the_alarm() -> None:
    report = {"filesystems": [{"mount": "/srv", "used": 60 * GB, "available": 40 * GB}]}
    limits = AlarmSettings()
    [p] = host_problems("nas", "NAS", report, limits, {"/srv": 9.6})
    assert p.title == "NAS: /srv will be full in about 10 days" and p.key == "host:nas:disk:/srv"
    assert host_problems("nas", "NAS", report, limits, {"/srv": 30}) == []
    assert host_problems("nas", "NAS", report, AlarmSettings(disk_days=0), {"/srv": 0.5}) == []
    full = {"filesystems": [{"mount": "/srv", "used": 90 * GB, "available": 10 * GB}]}
    [p] = host_problems("nas", "NAS", full, limits, {"/srv": 2})  # already full: that alarm
    assert p.title == "NAS: /srv is 90% full"


def test_invalid_saved_samples_do_not_break_polling(tmp_path: Path) -> None:
    import json

    for data in (
        [],
        {"nas": None},
        {"nas": {"/": [None, [], ["bad", 1, 2], ["2026-01-01", 1, 2], [NOW.isoformat(), "oops", 2]]}},
    ):
        (tmp_path / "disk-usage.json").write_text(json.dumps(data))
        disks = DiskHistory(tmp_path)
        disks.note("nas", {"filesystems": [{"mount": "/", "used": 1, "available": 2}]}, NOW)
        assert disks.forecasts("nas", NOW) == {}
