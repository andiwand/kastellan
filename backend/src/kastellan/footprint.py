"""What Kastellan costs the machine it runs on: the hub's CPU and memory, and its data folder,
store by store, with where each one levels off.

A store kept for a time (check history, central logs, vitals, internet samples) grows at the
rate it filled so far (what it holds over how long it spans) until its oldest data reaches the
retention, then levels off at that rate times the retention. A store kept by count (device and
hub backups, deduplicated) levels off when it holds that many versions. The rest (the audit and
alarm logs, small state files) only grows; that part decides whether the disk fills up.

CPU is a share of one core between two calls (like `top`), so the first report after a start
says nothing yet. Linux only reads memory from /proc; elsewhere the peak is shown instead."""

from __future__ import annotations

import json
import os
import resource
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import internet, vitals
from .vitals import Cpu

if TYPE_CHECKING:
    from .hub import Hub

DAY = 86400.0
MIN_SPAN = 3600.0  # seconds a store must span before its rate says anything


@dataclass
class Store:
    name: str  # what a person calls it
    path: str  # under the data folder
    used: int  # bytes now
    per_day: float | None  # bytes it grows by a day (None: not known yet)
    kept: str  # how long or how much is kept, in words
    steady: int | None  # bytes once it levels off (None: it doesn't)
    days_to_steady: float | None  # until it does (0: it has)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                continue
    return total


def _sqlite(path: Path) -> int:
    """A database with its write-ahead log."""
    return sum(_size(path.with_name(path.name + s)) for s in ("", "-wal", "-shm"))


def _oldest(path: Path, query: str) -> float | None:
    """The oldest timestamp in a database (read only; None when empty or unreadable)."""
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            row = db.execute(query).fetchone()
    except sqlite3.Error:
        return None
    return float(row[0]) if row and row[0] is not None else None


def by_time(name: str, path: str, used: int, oldest: float | None, keep_days: float, now: float) -> Store:
    """A store kept for `keep_days`: its rate so far, and where that levels off."""
    kept = f"{keep_days:g} days"
    span = now - oldest if oldest is not None else 0.0
    if used <= 0 or span < MIN_SPAN:
        return Store(name, path, used, None, kept, None, None)
    per_day = used / (span / DAY)
    steady = round(per_day * keep_days)
    return Store(name, path, used, per_day, kept, max(steady, used), max(0.0, keep_days - span / DAY))


def by_count(
    name: str, path: str, used: int, versions: int | None, keep: int, per_day: float | None
) -> Store:
    """A store kept by count (deduplicated versions): it holds `keep` at most."""
    kept = f"{keep} versions"
    if not versions or used <= 0:
        return Store(name, path, used, per_day, kept, None, None)
    if versions >= keep:
        return Store(name, path, used, per_day, kept, used, 0.0)
    steady = round(used * keep / versions)  # an upper bound: unchanged files are kept once
    days = (keep - versions) * (used / versions) / per_day if per_day else None
    return Store(name, path, used, per_day, kept, steady, days)


def growing(name: str, path: str, used: int, since: float | None, now: float) -> Store:
    """A store nothing removes from: it grows at its rate so far."""
    span = now - since if since is not None else 0.0
    per_day = used / (span / DAY) if used > 0 and span >= MIN_SPAN else None
    return Store(name, path, used, per_day, "everything", None, None)


def _day_start(files: list[Path]) -> float | None:
    """When the first of a store's day files (named YYYY-MM-DD) began."""
    try:
        return datetime.combine(date.fromisoformat(files[0].stem), datetime.min.time(), UTC).timestamp()
    except (IndexError, ValueError):
        return None


def _first_time(path: Path) -> float | None:
    """The time of a JSON-lines log's first line (its "time" field)."""
    try:
        with path.open() as f:
            first = json.loads(f.readline())
        return datetime.fromisoformat(first["time"]).timestamp()
    except (OSError, ValueError, KeyError, TypeError):
        return None


def stores(hub: Hub, now: float | None = None) -> list[Store]:
    """Every part of the data that grows, measured now, each where its owner keeps it. Blocking:
    call it in a thread."""
    now = time.time() if now is None else now
    config = hub.config
    data = hub.data

    def where(path: Path) -> str:
        return str(path.relative_to(data)) if path.is_relative_to(data) else path.name

    checks = hub.history.folder
    days = sorted(checks.glob("????-??-??.csv")) if checks.is_dir() else []
    logs, vitals_db = hub.logs.path, hub.vitals.path
    net = hub.internet.folder
    samples = sorted(net.glob("????-??-??.jsonl")) if net.is_dir() else []
    backups = hub.device_backups.folder
    snaps = hub.device_backups.counts()
    backups_used = _size(backups)
    per_day = None
    if snaps["oldest"] is not None and now - snaps["oldest"] >= MIN_SPAN:
        per_day = backups_used / ((now - snaps["oldest"]) / DAY)
    hub_copies = hub.hub_backup.folder
    out = [
        by_time("Check history", where(checks), _size(checks), _day_start(days), config.retention_days, now),
        by_time(
            "Central logs",
            where(logs),
            _sqlite(logs),
            _oldest(logs, "SELECT MIN(ts) FROM lines"),
            config.logs.days,
            now,
        ),
        by_time(
            "Vitals",
            where(vitals_db),
            _sqlite(vitals_db),
            _oldest(vitals_db, "SELECT MIN(ts) FROM vitals"),
            vitals.KEEP_DAYS,
            now,
        ),
        by_time(
            "Internet samples", where(net), _size(net), _day_start(samples), internet.KEEP_SAMPLES_DAYS, now
        ),
        by_count(
            "Device backups", where(backups), backups_used, snaps["most"], config.device_backups.keep, per_day
        ),
        by_count(
            "Kastellan's own backup",
            where(hub_copies),
            _size(hub_copies),
            hub.hub_backup.generations(),
            config.hub_backup.keep,
            None,
        ),
        growing("Audit log", where(hub.audit.path), _size(hub.audit.path), _first_time(hub.audit.path), now),
        growing(
            "Alarm log",
            where(hub.alarms.log_path),
            _size(hub.alarms.log_path),
            _first_time(hub.alarms.log_path),
            now,
        ),
    ]
    counted = {s.path.split("/")[0] for s in out} | {
        f"{p.name}-{x}" for p in (logs, vitals_db) for x in ("wal", "shm")
    }
    rest = sum(_size(p) for p in data.iterdir() if p.name not in counted) if data.is_dir() else 0
    out.append(Store("Everything else", "", rest, None, "small state: keys, settings, caches", rest, 0.0))
    return out


def forecast(items: list[Store], free: int | None) -> dict[str, Any]:
    """The data folder as a whole: what it holds, grows by, levels off at, and when the disk
    would be full (only what keeps growing can fill it, once the rest has levelled off)."""
    used = sum(s.used for s in items)
    per_day = sum(s.per_day or 0 for s in items if s.steady is None or (s.days_to_steady or 0) > 0)
    unbounded = sum(s.per_day or 0 for s in items if s.steady is None)
    bounded_growth = sum((s.steady or 0) - s.used for s in items if s.steady is not None)
    steady = sum(s.steady if s.steady is not None else s.used for s in items)
    days_full = None
    if free is not None:
        headroom = free - max(0, bounded_growth)
        if headroom < 0 and per_day > 0:
            days_full = free / per_day  # full before the rest levels off
        elif headroom >= 0 and unbounded > 0:
            days_full = headroom / unbounded
    levelled = [s.days_to_steady for s in items if s.steady is not None and s.days_to_steady is not None]
    return {
        "used": used,
        "per_day": round(per_day),
        "steady": steady,  # with what keeps growing as it is now
        "growing_per_day": round(unbounded),
        "days_to_steady": round(max(levelled), 1) if levelled else None,
        "days_until_full": round(days_full) if days_full is not None else None,
    }


class Resources:
    """The hub process's CPU and memory, and the machine's."""

    def __init__(self) -> None:
        self._cpu = Cpu()
        self._last: tuple[float, float] | None = None  # (wall clock, process CPU seconds)
        # A first reading now: the first report then says the mean since the hub started.
        self._process_cpu()
        self._machine()

    def _process_cpu(self) -> float | None:
        t = os.times()
        now, used = time.monotonic(), t.user + t.system + t.children_user + t.children_system
        last, self._last = self._last, (now, used)
        if last is None or now <= last[0]:
            return None
        return round(100 * (used - last[1]) / (now - last[0]), 1)  # % of one core

    @staticmethod
    def _rss() -> tuple[int | None, bool]:
        """The process's resident memory now (Linux), or its peak elsewhere."""
        try:
            pages = int(Path("/proc/self/statm").read_text().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE"), False
        except (OSError, ValueError, IndexError):
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return (peak if os.uname().sysname == "Darwin" else peak * 1024), True

    def _machine(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "cores": os.cpu_count(),
            "cpu": None,
            "memory_total": None,
            "memory_available": None,
        }
        try:
            counters = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
            out["cpu"] = self._cpu.percent("machine", counters)
            mem = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, _, value = line.partition(":")
                mem[key] = int(value.split()[0]) * 1024
            out["memory_total"], out["memory_available"] = mem.get("MemTotal"), mem.get("MemAvailable")
        except (OSError, ValueError, IndexError):
            pass
        return out

    def report(self, hub: Hub) -> dict[str, Any]:
        """Now: the hub, the machine, and the data folder. Blocking: call it in a thread."""
        rss, peak = self._rss()
        data = hub.data
        try:
            disk = shutil.disk_usage(data)
            total, free = disk.total, disk.free
        except OSError:
            total = free = None
        items = stores(hub)
        return {
            "time": time.time(),
            "hub": {"cpu": self._process_cpu(), "memory": rss, "memory_is_peak": peak},
            "machine": self._machine(),
            "disk": {"path": str(data), "total": total, "free": free},
            "stores": [
                asdict(s) | {"per_day": round(s.per_day) if s.per_day is not None else None} for s in items
            ],
            "forecast": forecast(items, free),
        }
