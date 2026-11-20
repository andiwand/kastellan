"""Check results as CSV, one file per day: <data>/checks/<YYYY-MM-DD>.csv.

The folder is the index, as in Norn: a day is a file, retention deletes whole files. At ~20 devices
this stays small (a few MB a day at a 60 s interval). If queries over long ranges get slow, this
module is the one place to swap for SQLite.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

FIELDS = ["time", "device", "check", "up", "latency_ms", "detail"]


@dataclass(frozen=True)
class Row:
    time: datetime
    device: str
    check: str
    up: bool
    latency_ms: float | None
    detail: str


class History:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _file(self, day: date) -> Path:
        return self.folder / f"{day.isoformat()}.csv"

    def append(self, row: Row) -> None:
        self.extend([row])

    def extend(self, rows: list[Row]) -> None:
        """Append rows (in time order) to their days' files. Days are UTC, like the times."""
        self.folder.mkdir(parents=True, exist_ok=True)
        by_day: dict[date, list[Row]] = {}
        for row in rows:
            by_day.setdefault(row.time.astimezone(UTC).date(), []).append(row)
        for day, day_rows in by_day.items():
            path = self._file(day)
            new = not path.exists()
            with path.open("a", newline="") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(FIELDS)
                writer.writerows(
                    [
                        r.time.isoformat(timespec="seconds"),
                        r.device,
                        r.check,
                        int(r.up),
                        "" if r.latency_ms is None else r.latency_ms,
                        r.detail,
                    ]
                    for r in day_rows
                )

    def read(self, since: datetime, device: str | None = None) -> Iterator[Row]:
        day = since.astimezone(UTC).date()
        today = datetime.now(UTC).date()
        while day <= today:
            path = self._file(day)
            if path.exists():
                with path.open(newline="") as f:
                    for r in csv.DictReader(f):
                        if device is not None and r["device"] != device:
                            continue
                        t = datetime.fromisoformat(r["time"])
                        if t < since:
                            continue
                        yield Row(
                            t,
                            r["device"],
                            r["check"],
                            r["up"] == "1",
                            float(r["latency_ms"]) if r["latency_ms"] else None,
                            r["detail"],
                        )
            day += timedelta(days=1)

    def prune(self, keep_days: int, today: date | None = None) -> list[Path]:
        """Delete the files older than keep_days; returns what it deleted."""
        if not self.folder.exists():
            return []
        cutoff = (today or date.today()) - timedelta(days=keep_days)
        gone = []
        for path in self.folder.glob("*.csv"):
            try:
                day = date.fromisoformat(path.stem)
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                gone.append(path)
        return gone
