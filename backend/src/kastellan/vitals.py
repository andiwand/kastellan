"""A device's vitals: how busy its CPU is, how full its memory and storage are, how warm it runs.
One shape for every kind of device, from what each tells:

- a Linux host's collector: CPU time counters (/proc/stat), memory, filesystems, temperatures;
- an OpenWrt device: `system info` (memory, the root filesystem) and its read-only `vitals`
  helper (CPU time counters, the hottest thermal zone);
- a FRITZ!Box: its web interface's energy monitor (`ecoStat`: CPU use, RAM use, temperature).

CPU time counters become a percentage between two readings (`Cpu`), so the value is the mean
over the time between them, not a moment's. The hub keeps one sample per device at most every
SAMPLE_EVERY seconds in SQLite (`Store`, KEEP_DAYS) for the charts and the devices list's trends.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

KEEP_DAYS = 30
SAMPLE_EVERY = 110  # seconds: at most one sample per device (routers are read every 120 s)
FIELDS = ("cpu", "memory", "storage", "temperature")
DISK_FIELDS = ("busy", "asleep", "temperature")


@dataclass
class Vitals:
    cpu: float | None = None  # percent busy, mean since the last reading
    load: float | None = None  # 1-minute load average (where there is no CPU percentage yet)
    cores: int | None = None
    memory_used: int | None = None  # bytes
    memory_total: int | None = None
    memory_share: float | None = None  # percent, where only that is known (a FRITZ!Box)
    storage_used: int | None = None  # bytes, of the fullest filesystem that counts
    storage_total: int | None = None
    storage_mount: str | None = None
    temperature: float | None = None  # °C, the hottest sensor

    @property
    def memory(self) -> float | None:
        if self.memory_share is not None:
            return self.memory_share
        return _pct(self.memory_used, self.memory_total)

    @property
    def storage(self) -> float | None:
        return _pct(self.storage_used, self.storage_total)

    def empty(self) -> bool:
        return all(v is None for v in (self.cpu, self.load, self.memory, self.storage, self.temperature))

    def view(self) -> dict[str, Any]:
        return {**asdict(self), "memory": _round(self.memory), "storage": _round(self.storage)}


def _pct(used: int | float | None, total: int | float | None) -> float | None:
    if _num(used) is None or _num(total) is None or used < 0 or total <= 0:
        return None
    return max(0.0, min(100.0, 100 * used / total))


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _num(v: Any) -> float | None:
    if not isinstance(v, int | float) or isinstance(v, bool):
        return None
    try:
        value = float(v)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


@dataclass
class DiskSample:
    disk: str
    busy: float | None  # percent of the time doing I/O since the reading before
    asleep: bool | None  # a spinning disk in standby (None: not spinning, or not known)
    temperature: float | None


class Busy:
    """Disks' busy shares from their I/O time counters (ms), per device and disk."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], tuple[float, int]] = {}  # (time, busy ms)

    def percent(self, device: str, disk: str, busy_ms: int | None, now: float) -> float | None:
        if busy_ms is None:
            return None
        last = self._last.get((device, disk))
        self._last[(device, disk)] = (now, busy_ms)
        if last is None or now <= last[0] or busy_ms < last[1]:
            return None  # the first reading, or the counter began anew (a restart)
        return round(max(0.0, min(100.0, 100 * (busy_ms - last[1]) / ((now - last[0]) * 1000))), 1)


def disks_of(device: str, report: dict[str, Any], busy: Busy, now: float) -> list[DiskSample]:
    """A Linux host's disks from its collector report (activity, and SMART's temperature)."""
    temps = {
        d.get("disk"): _num(d.get("temperature"))
        for d in (report.get("smart") or {}).get("disks") or []
        if isinstance(d, dict)
    }
    return [
        DiskSample(
            disk=d["disk"],
            busy=busy.percent(device, d["disk"], d.get("busy_ms"), now),
            asleep=d.get("asleep") if d.get("rotational") else None,
            temperature=temps.get(d["disk"]),
        )
        for d in report.get("activity") or []
        if isinstance(d, dict) and d.get("disk")
    ]


class Cpu:
    """CPU percentages from time counters (user nice system idle iowait irq softirq steal), per
    device: busy share of the time between this reading and the last one."""

    def __init__(self) -> None:
        self._last: dict[str, tuple[int, int]] = {}

    def percent(self, device: str, counters: Any) -> float | None:
        if not isinstance(counters, list) or len(counters) < 4:
            return None
        values = counters[:8]
        if not all(type(c) is int and 0 <= c < 2**64 for c in values):
            return None
        total, idle = sum(values), values[3] + (values[4] if len(values) > 4 else 0)
        last = self._last.get(device)
        self._last[device] = (total, idle)
        if last is None or total <= last[0] or idle < last[1]:
            return None  # the first reading, or the device restarted (its counters began anew)
        busy = 1 - (idle - last[1]) / (total - last[0])
        return round(max(0.0, min(100.0, 100 * busy)), 1)

    def forget(self, keep: set[str]) -> None:
        self._last = {k: v for k, v in self._last.items() if k in keep}


def from_host(device: str, report: dict[str, Any], cpu: Cpu) -> Vitals:
    """A Linux host's collector report."""
    sys = report.get("system") or {}
    mem = sys.get("memory") or {}
    v = Vitals(
        cpu=cpu.percent(device, sys.get("cpu")),
        load=_num((sys.get("load") or [None])[0]),
        cores=sys.get("cpus") if isinstance(sys.get("cpus"), int) else None,
    )
    total, available = mem.get("total"), mem.get("available")
    if type(total) is int and type(available) is int and 0 <= available <= total and total > 0:
        v.memory_used, v.memory_total = total - available, total
    fullest = None
    for fs in report.get("filesystems") or []:
        if not isinstance(fs, dict):
            continue
        used, free = fs.get("used"), fs.get("available", fs.get("free"))
        if type(used) is not int or type(free) is not int or min(used, free) < 0 or used + free <= 0:
            continue
        if fullest is None or used / (used + free) > fullest[0]:
            fullest = (used / (used + free), used, used + free, fs.get("mount"))
    if fullest:
        _, v.storage_used, v.storage_total, v.storage_mount = fullest
    temps = [_num(t.get("celsius")) for t in report.get("temperatures") or [] if isinstance(t, dict)]
    temps = [t for t in temps if t is not None and -40 < t < 150]
    v.temperature = max(temps) if temps else None
    return v


def from_openwrt(device: str, router: dict[str, Any], helper: dict[str, Any] | None, cpu: Cpu) -> Vitals:
    """An OpenWrt device's report (system info) and its vitals helper's answer (or None)."""
    sys = router.get("system") or {}
    mem = sys.get("memory") or {}
    v = Vitals(load=_num((sys.get("load") or [None])[0]))
    total = mem.get("total")
    if type(total) is int and total > 0:
        available = mem.get("available")
        if type(available) is not int:  # before OpenWrt 21.02: free + buffered (+ cached)
            parts = [mem.get(k) for k in ("free", "buffered", "cached")]
            parts = [p for p in parts if type(p) is int and p >= 0]
            available = sum(parts) if parts else None
        if type(available) is int and 0 <= available <= total:
            v.memory_used, v.memory_total = max(0, total - available), total
    root = router.get("storage") or {}
    if isinstance(root.get("total"), int) and root["total"] > 0 and isinstance(root.get("used"), int):
        # system info gives KiB
        v.storage_used, v.storage_total, v.storage_mount = root["used"] * 1024, root["total"] * 1024, "/"
    if helper:
        v.cpu = cpu.percent(device, helper.get("cpu"))
        t = _num(helper.get("temp"))
        if t is not None and 0 < t < 150_000:
            v.temperature = round(t / 1000, 1)  # the helper always returns millidegrees
    return v


def from_fritz(eco: dict[str, Any] | None) -> Vitals:
    """A FRITZ!Box's energy monitor (`data.lua` page `ecoStat`): each value a series of recent
    readings, the newest last. `ramusage` has fixed, dynamic and free shares (percent)."""
    v = Vitals()
    if not isinstance(eco, dict):
        return v

    def newest(key: str, index: int = 0) -> float | None:
        series = (eco.get(key) or {}).get("series") if isinstance(eco.get(key), dict) else None
        if not isinstance(series, list) or len(series) <= index or not isinstance(series[index], list):
            return None
        return _num(series[index][-1]) if series[index] else None

    cpu = newest("cpuutil")
    v.cpu = cpu if cpu is not None and 0 <= cpu <= 100 else None
    temp = newest("cputemp")
    v.temperature = temp if temp is not None and 0 < temp < 150 else None
    free = newest("ramusage", 2)
    if free is not None and 0 <= free <= 100:
        v.memory_share = 100.0 - free
    return v


class Store:
    """Samples in SQLite (`<data>/vitals.db`). Blocking: the hub calls it in a thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._last: dict[str, float] = {}
        self._last_disks: dict[str, float] = {}
        self._pruned = 0.0
        self._closed = False

    @property
    def _db(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("vitals store is closed")
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS vitals (device TEXT NOT NULL, ts REAL NOT NULL,"
                " cpu REAL, memory REAL, storage REAL, temperature REAL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS vitals_device_ts ON vitals (device, ts)")
            # Each disk of a host (its collector): how busy, asleep (1) or spinning (0), how warm.
            db.execute(
                "CREATE TABLE IF NOT EXISTS disks (device TEXT NOT NULL, disk TEXT NOT NULL,"
                " ts REAL NOT NULL, busy REAL, asleep REAL, temperature REAL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS disks_device_ts ON disks (device, ts)")
            self._conn = db
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(self, device: str, v: Vitals, now: float | None = None) -> bool:
        """Keep it, unless the device's last sample is newer than SAMPLE_EVERY."""
        now = time.time() if now is None else now
        if v.empty():
            return False
        with self._lock:
            if self._closed or now - self._last.get(device, 0) < SAMPLE_EVERY:
                return False
            self._db.execute(
                "INSERT INTO vitals VALUES (?, ?, ?, ?, ?, ?)",
                (device, now, v.cpu, v.memory, v.storage, v.temperature),
            )
            self._last[device] = now
            if now - self._pruned > 3600:
                for table in ("vitals", "disks"):
                    self._db.execute(f"DELETE FROM {table} WHERE ts < ?", (now - KEEP_DAYS * 86400,))
                self._pruned = now
        return True

    def series(
        self, devices: list[str] | None, since: float, step: float, buckets: int
    ) -> dict[str, dict[str, list[float | None]]]:
        """Per device and field, the mean of each of `buckets` slices of `step` seconds from
        `since` (None where nothing was read)."""
        where, args = "ts >= ? AND ts < ?", [since, since + step * buckets]
        if devices is not None:
            if not devices:
                return {}
            where += f" AND device IN ({','.join('?' * len(devices))})"
            args += devices
        with self._lock:
            rows = self._db.execute(
                f"SELECT device, CAST((ts - ?) / ? AS INTEGER) AS b, AVG(cpu), AVG(memory), AVG(storage),"
                f" AVG(temperature) FROM vitals WHERE {where} GROUP BY device, b",
                [since, step, *args],
            ).fetchall()
        out: dict[str, dict[str, list[float | None]]] = {}
        for device, b, *values in rows:
            if not 0 <= b < buckets:
                continue
            d = out.setdefault(device, {f: [None] * buckets for f in FIELDS})
            for f, value in zip(FIELDS, values, strict=True):
                d[f][b] = None if value is None else round(value, 1)
        return out

    def add_disks(self, device: str, samples: list[DiskSample], now: float | None = None) -> bool:
        """A host's disks now, unless its last sample is newer than SAMPLE_EVERY."""
        now = time.time() if now is None else now
        if not samples:
            return False
        with self._lock:
            if self._closed or now - self._last_disks.get(device, 0) < SAMPLE_EVERY:
                return False
            self._db.executemany(
                "INSERT INTO disks VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        device,
                        d.disk,
                        now,
                        d.busy,
                        None if d.asleep is None else float(d.asleep),
                        d.temperature,
                    )
                    for d in samples
                ],
            )
            self._last_disks[device] = now
        return True

    def disk_series(
        self, device: str, since: float, step: float, buckets: int
    ) -> dict[str, dict[str, list[float | None]]]:
        """Per disk of the device: mean busy (%), share asleep (%) and mean temperature (°C) in
        each of `buckets` slices of `step` seconds from `since`."""
        with self._lock:
            rows = self._db.execute(
                "SELECT disk, CAST((ts - ?) / ? AS INTEGER) AS b, AVG(busy), AVG(asleep) * 100,"
                " AVG(temperature) FROM disks WHERE device = ? AND ts >= ? AND ts < ? GROUP BY disk, b",
                [since, step, device, since, since + step * buckets],
            ).fetchall()
        out: dict[str, dict[str, list[float | None]]] = {}
        for disk, b, *values in rows:
            if not 0 <= b < buckets:
                continue
            d = out.setdefault(disk, {f: [None] * buckets for f in DISK_FIELDS})
            for f, value in zip(DISK_FIELDS, values, strict=True):
                d[f][b] = None if value is None else round(value, 1)
        return out
