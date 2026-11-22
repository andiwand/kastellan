"""Disk usage over time, and when a filesystem will be full at the rate it fills.

The collector's report says how full each filesystem is now; the hub keeps one sample an hour
per host and mount in <data>/disk-usage.json (KEEP_DAYS), and fits a line through the last
WINDOW_DAYS: `forecast` is the days left, or None when it doesn't fill (or can't tell yet)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EVERY = timedelta(hours=1)  # one sample an hour
KEEP_DAYS = 30
WINDOW_DAYS = 7  # the rate is from this much history
MIN_SPAN = timedelta(hours=24)  # less history says too little
MAX_DAYS = 365  # further out is no forecast


def forecast(samples: list[tuple[datetime, int, int]], now: datetime) -> float | None:
    """Days until full from (time, used, available) samples, oldest first. A clean-up (used
    dropped by 5% of the size or more) starts the history afresh: the rate before it says
    nothing about the disk after it."""
    recent = [s for s in samples if timedelta(0) <= now - s[0] <= timedelta(days=WINDOW_DAYS)]
    for i in range(len(recent) - 1, 0, -1):
        (_, u0, a0), (_, u1, _) = recent[i - 1], recent[i]
        if u0 - u1 >= 0.05 * (u0 + a0):
            recent = recent[i:]
            break
    if len(recent) < 6 or recent[-1][0] - recent[0][0] < MIN_SPAN:
        return None
    xs = [(t - recent[0][0]).total_seconds() / 86400 for t, _, _ in recent]
    ys = [used for _, used, _ in recent]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / var  # bytes a day
    available = recent[-1][2]
    if slope <= 0 or available / slope > MAX_DAYS:
        return None
    return available / slope


def _pools_once(filesystems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ZFS datasets of one pool share its space: the pool's first only (as the disk alarms)."""
    out, pools = [], set()
    for fs in filesystems:
        if fs.get("type") == "zfs":
            pool = str(fs.get("device", "")).split("/", 1)[0]
            if pool in pools:
                continue
            pools.add(pool)
        out.append(fs)
    return out


class DiskHistory:
    def __init__(self, folder: Path) -> None:
        self.path = folder / "disk-usage.json"
        self.data: dict[str, dict[str, list[list[Any]]]] = {}  # device → mount → [[time, used, avail]]
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            for device, mounts in data.items():
                if not isinstance(mounts, dict):
                    continue
                clean = {}
                for mount, rows in mounts.items():
                    if not isinstance(rows, list):
                        continue
                    valid = []
                    for row in rows:
                        try:
                            t, used, available = row
                            at = datetime.fromisoformat(t)
                            if at.tzinfo is not None and all(
                                type(n) is int and n >= 0 for n in (used, available)
                            ):
                                valid.append([t, used, available])
                        except (TypeError, ValueError):
                            continue
                    clean[mount] = sorted(valid, key=lambda r: datetime.fromisoformat(r[0]))
                self.data[device] = clean
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("cannot read %s (%s); starting afresh", self.path, e)

    def note(self, device: str, report: dict[str, Any], now: datetime) -> None:
        """Take a sample from a collector report, if the last is an hour old."""
        mounts = self.data.setdefault(device, {})
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        changed = False
        for fs in _pools_once(report.get("filesystems") or []):
            try:
                mount, used, avail = str(fs["mount"]), int(fs["used"]), int(fs["available"])
            except (KeyError, TypeError, ValueError):
                continue
            rows = mounts.setdefault(mount, [])
            if rows and now - datetime.fromisoformat(rows[-1][0]) < EVERY:
                continue
            rows.append([now.isoformat(timespec="seconds"), used, avail])
            mounts[mount] = [r for r in rows if r[0] >= cutoff]
            changed = True
        if changed:
            # A mount no longer reported goes once its newest sample is past KEEP_DAYS.
            for mount in [m for m, rows in mounts.items() if not rows or rows[-1][0] < cutoff]:
                del mounts[mount]
            self._save()

    def forecasts(self, device: str, now: datetime) -> dict[str, float]:
        """Days until full per mount, for those that fill."""
        out = {}
        for mount, rows in self.data.get(device, {}).items():
            days = forecast([(datetime.fromisoformat(t), u, a) for t, u, a in rows], now)
            if days is not None:
                out[mount] = round(days, 1)
        return out

    def forget(self, keep: set[str]) -> None:
        """Devices no longer there (or without a collector): their history goes."""
        gone = set(self.data) - keep
        if gone:
            for d in gone:
                del self.data[d]
            self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, separators=(",", ":")))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot save the disk usage: %s", e)
