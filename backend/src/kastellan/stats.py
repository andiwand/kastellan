"""Summaries of the history for the web app: buckets (uptime and latency over time) and changes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .history import Row


@dataclass
class Bucket:
    checks: int = 0
    up: int = 0
    latency_sum: float = 0
    latency_n: int = 0

    def summary(self) -> dict[str, float | None]:
        return {
            "uptime": round(self.up / self.checks, 4) if self.checks else None,
            "latency_ms": round(self.latency_sum / self.latency_n, 2) if self.latency_n else None,
        }


def series(
    rows: Iterable[Row], start: datetime, step: timedelta, count: int
) -> dict[str, dict[str, list[dict[str, float | None]]]]:
    """Per device and check, `count` buckets of `step` from `start`: the share of checks that were
    up and the mean latency of those that were. A bucket without checks has None for both."""
    out: dict[str, dict[str, list[Bucket]]] = {}
    for row in rows:
        i = int((row.time - start) / step)
        if not 0 <= i < count:
            continue
        checks = out.setdefault(row.device, {})
        buckets = checks.get(row.check)
        if buckets is None:  # not setdefault: that would build the list for every row
            buckets = checks[row.check] = [Bucket() for _ in range(count)]
        bucket = buckets[i]
        bucket.checks += 1
        if row.up:
            bucket.up += 1
            if row.latency_ms is not None:
                bucket.latency_sum += row.latency_ms
                bucket.latency_n += 1
    return {
        device: {check: [b.summary() for b in buckets] for check, buckets in checks.items()}
        for device, checks in out.items()
    }


def changes(rows: Iterable[Row]) -> list[dict[str, object]]:
    """The moments a check went up or down (rows in time order), newest first. The first result of a
    check in the range is not a change."""
    last: dict[tuple[str, str], bool] = {}
    out = []
    for row in rows:
        key = (row.device, row.check)
        if key in last and last[key] != row.up:
            out.append(
                {
                    "kind": "state",
                    "time": row.time,
                    "device": row.device,
                    "check": row.check,
                    "up": row.up,
                    "detail": row.detail,
                }
            )
        last[key] = row.up
    out.reverse()
    return out
