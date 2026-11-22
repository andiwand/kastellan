"""Kastellan's own footprint: each store's rate, where it levels off, and when the disk fills."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from kastellan.footprint import by_count, by_time, forecast, growing

DAY = 86400


def test_a_store_kept_for_a_time_levels_off() -> None:
    now = 100 * DAY
    s = by_time("Logs", "logs.db", 2_000_000, now - 2 * DAY, 14, now)  # 2 MB over 2 days
    assert (s.per_day, s.steady, s.days_to_steady) == (1_000_000, 14_000_000, 12)
    full = by_time("Logs", "logs.db", 14_000_000, now - 20 * DAY, 14, now)  # older than kept: levelled
    assert full.days_to_steady == 0 and full.steady == 14_000_000
    assert by_time("Logs", "logs.db", 10, now - 60, 14, now).per_day is None  # too new to tell


def test_a_store_kept_by_count_and_one_that_only_grows() -> None:
    half = by_count("Backups", "device-backups", 3_000, versions=30, keep=60, per_day=100)
    assert (half.steady, half.days_to_steady) == (6_000, 30)
    assert by_count("Backups", "device-backups", 3_000, versions=60, keep=60, per_day=100).days_to_steady == 0
    assert by_count("Hub", "hub-backup", 3_000, versions=None, keep=30, per_day=None).steady is None
    audit = growing("Audit log", "audit.log", 10 * 1000, 0, 10 * DAY)
    assert audit.per_day == 1000 and audit.steady is None


def test_the_whole_folder() -> None:
    logs = by_time("Logs", "logs.db", 2_000_000, 98 * DAY, 14, 100 * DAY)  # levels off at 14 MB
    audit = growing("Audit log", "audit.log", 10_000, 90 * DAY, 100 * DAY)  # 1 kB a day, for ever
    f = forecast([logs, audit], free=1_012_000_000)
    assert f["steady"] == 14_010_000 and f["days_to_steady"] == 12 and f["growing_per_day"] == 1000
    assert f["days_until_full"] == 1_000_000  # what is free once the logs level off, at 1 kB a day
    assert forecast([logs], free=1_000_000)["days_until_full"] == 1  # full before they level off
    assert forecast([logs], free=10**12)["days_until_full"] is None  # it all levels off


def test_the_report(admin_client, tmp_path: Path) -> None:
    c = admin_client()
    data = tmp_path
    (data / "checks").mkdir(exist_ok=True)
    (data / "checks" / "2026-01-01.csv").write_text("x" * 1000)
    first = datetime.fromtimestamp(time.time() - 4 * DAY, UTC).isoformat()
    (data / "audit.log").write_text(json.dumps({"time": first}) + "\n" + "y" * 3000)
    c.get("/api/hub/resources")  # the first: CPU is a share between two reports
    r = c.get("/api/hub/resources").json()
    names = {s["name"]: s for s in r["stores"]}
    assert names["Check history"]["used"] == 1000 and names["Audit log"]["steady"] is None
    assert 700 < names["Audit log"]["per_day"] < 900  # ~3 kB over four days
    assert r["hub"]["memory"] > 0 and r["hub"]["cpu"] is not None
    assert r["disk"]["free"] > 0 and r["forecast"]["used"] >= 4000
    c.post("/api/auth/logout")
    assert c.get("/api/hub/resources").status_code in (401, 403)


def test_database_paths_with_uri_characters(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from kastellan.footprint import _oldest

    path = tmp_path / "vitals#?.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE vitals (ts REAL)")
        db.execute("INSERT INTO vitals VALUES (1234)")
        db.commit()
    assert _oldest(path, "SELECT MIN(ts) FROM vitals") == 1234
