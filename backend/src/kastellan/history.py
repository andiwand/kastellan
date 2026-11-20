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


def _end_line(path: Path) -> None:
    """A crash can leave the last line half written; start on a fresh line so the damage stays
    in that one line."""
    with path.open("rb+") as f:
        if f.seek(0, 2) == 0:
            return
        f.seek(-1, 2)
        if f.read(1) != b"\n":
            f.write(b"\n")


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
            if not new:
                _end_line(path)
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
                yield from self._read_file(path, since, device)
            day += timedelta(days=1)

    @staticmethod
    def _read_file(path: Path, since: datetime, device: str | None) -> Iterator[Row]:
        # NUL bytes (a power cut mid-write) would stop the csv module; drop them, and skip any
        # line that does not parse rather than losing the whole day.
        with path.open(newline="", errors="replace") as f:
            # For one device, skip other devices' lines before parsing them (the costly part).
            needle = f",{device}," if device is not None else None
            lines = (
                line.replace("\0", "") for n, line in enumerate(f) if n == 0 or not needle or needle in line
            )
            for r in csv.DictReader(lines):
                if device is not None and r.get("device") != device:
                    continue
                try:
                    t = datetime.fromisoformat(r["time"])
                    if t < since:
                        continue
                    latency = r["latency_ms"]
                    row = Row(
                        t,
                        r["device"],
                        r["check"],
                        r["up"] == "1",
                        float(latency) if latency else None,
                        r["detail"] or "",
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                yield row

    def prune(self, keep_days: int, today: date | None = None) -> list[Path]:
        """Delete the files older than keep_days; returns what it deleted."""
        if not self.folder.exists():
            return []
        cutoff = (today or datetime.now(UTC).date()) - timedelta(days=keep_days)  # files are UTC days
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
