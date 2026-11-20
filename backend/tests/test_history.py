from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from kastellan.history import History, Row


def test_append_read_prune(tmp_path: Path) -> None:
    history = History(tmp_path / "checks")
    now = datetime.now(UTC).replace(microsecond=0)
    history.append(Row(now - timedelta(days=1), "nas", "ping", True, 1.5, ""))
    history.append(Row(now, "nas", "ping", False, None, "no reply"))
    history.append(Row(now, "router", "ping", True, 0.4, ""))

    rows = list(history.read(now - timedelta(days=2), device="nas"))
    assert [(r.up, r.latency_ms, r.detail) for r in rows] == [(True, 1.5, ""), (False, None, "no reply")]
    assert len(list(history.read(now - timedelta(minutes=1)))) == 2

    gone = history.prune(keep_days=0, today=now.date())
    assert [p.stem for p in gone] == [(now - timedelta(days=1)).date().isoformat()]
    assert history.prune(keep_days=0, today=date(2000, 1, 1)) == []


def test_series_and_changes() -> None:
    from kastellan.stats import changes, series

    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        Row(start + timedelta(minutes=m), "nas", "ping", up, lat, "")
        for m, up, lat in [(0, True, 1.0), (5, True, 3.0), (10, False, None), (35, True, 2.0)]
    ]
    s = series(rows, start, timedelta(minutes=30), 2)
    assert s == {"nas": {"ping": [{"uptime": 0.6667, "latency_ms": 2.0}, {"uptime": 1.0, "latency_ms": 2.0}]}}
    assert [(c["up"], c["time"].minute) for c in changes(rows)] == [(True, 35), (False, 10)]


def test_survives_a_half_written_line(tmp_path: Path) -> None:
    history = History(tmp_path)
    now = datetime.now(UTC).replace(microsecond=0)
    history.append(Row(now, "nas", "ping", True, 1.0, ""))
    path = next(tmp_path.glob("*.csv"))
    with path.open("a") as f:
        f.write("2026-11-20T10:0")  # the crash
    history.append(Row(now, "nas", "ping", False, None, "no reply"))
    with path.open("a") as f:
        f.write("\0\0\0\n")  # a power cut
    history.append(Row(now, "nas", "ping", True, 2.0, ""))
    rows = list(history.read(now - timedelta(minutes=1)))
    assert [r.up for r in rows] == [True, False, True]
