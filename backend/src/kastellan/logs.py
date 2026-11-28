"""Central logs: every device's log lines in one place, interleaved by time (the Logs page).

Lines come in three ways (logs_poll.py brings them in):
- fetched by the hub over channels it already authenticates and encrypts: OpenWrt's `log read`
  (its login's `logs` ability, openwrt/client.py), a FRITZ!Box's event log (fritzbox/), Linux
  hosts' journals (the actor's `journal` ability) and containers' own logs (`docker-logs`);
  WireGuard has no log, so lines are made from wg-easy's handshakes (presence, not a login);
- received over TLS with a client certificate per device (syslog_tls.py): verified, the
  certificate says who sent them;
- received on the open UDP syslog port (`logs.syslog` in config.yaml, off by default): plain
  syslog is neither encrypted nor authenticated, so such lines are only taken from known device
  addresses and kept as `verified = False`.

They are kept in SQLite (`<data>/logs.db`): one table of lines, indexed by time and by device,
pruned by age and by count (`logs.days`, `logs.max_lines`). Nothing here judges a line; the
page filters by device, source, program, level, time and text. Who read what goes to the audit
log (logs_api.py), never the lines.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# syslog's severities, 0 the worst.
LEVELS = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")
MAX_MSG = 2000
PROGRAM = re.compile(r"^([A-Za-z0-9_.()/@:-]{1,64}?)(?:\[(\d+)\])?: (.*)$", re.S)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class Line:
    ts: float  # seconds since the epoch, as the device says
    device: str
    source: str  # where the device got it: syslog, kernel, fritzbox, vpn, ...
    program: str  # dropbear, openvpn(server), kernel, ...
    level: int  # 0 (emerg) .. 7 (debug)
    msg: str
    verified: bool = True  # False: arrived on the open syslog port


def split_program(msg: str, default: str = "") -> tuple[str, str]:
    """`dropbear[1234]: Exit before auth` -> ("dropbear", "Exit before auth")."""
    m = PROGRAM.match(msg)
    if m:
        return m.group(1), m.group(3)
    return default, msg


def clean(text: str) -> str:
    return CONTROL.sub(" ", text).strip()[:MAX_MSG]


AHEAD = 86400  # a line's time beyond the hub's clock by more than this is garbage


class LogStore:
    """The lines in SQLite. Every method is blocking: the hub calls them in a thread."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    @property
    def _db(self) -> sqlite3.Connection:
        """Opened on first use (a data folder that can't be written doesn't stop the hub)."""
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
            self.path.chmod(0o600)  # WAL files inherit these permissions on creation.
            db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS lines (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    device TEXT NOT NULL,
                    source TEXT NOT NULL,
                    program TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    msg TEXT NOT NULL,
                    verified INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS lines_ts ON lines (ts);
                CREATE INDEX IF NOT EXISTS lines_device_ts ON lines (device, ts);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            with contextlib.suppress(OSError):
                self.path.chmod(0o600)
            self._conn = db
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def add(self, lines: list[Line]) -> int:
        if not lines:
            return 0
        rows = [
            (
                float(ln.ts),
                ln.device,
                ln.source[:32],
                ln.program[:64],
                min(7, max(0, int(ln.level))),
                clean(ln.msg),
                int(ln.verified),
            )
            for ln in lines
            if math.isfinite(ln.ts)
        ]
        with self._lock:
            self._db.executemany(
                "INSERT INTO lines (ts, device, source, program, level, msg, verified)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def query(
        self,
        devices: list[str] | None = None,
        sources: list[str] | None = None,
        programs: list[str] | None = None,
        max_level: int = 7,
        since: float | None = None,
        until: float | None = None,
        text: str | None = None,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Matching lines, newest first (by time, then by arrival); `before_id` pages back,
        `after_id` follows (oldest of the new first is not needed: the page sorts)."""
        where, args = ["level <= ?"], [max_level]
        for column, values in (("device", devices), ("source", sources), ("program", programs)):
            if values:
                where.append(f"{column} IN ({','.join('?' * len(values))})")
                args.extend(values)
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if until is not None:
            where.append("ts <= ?")
            args.append(until)
        if text:
            for word in text.split()[:5]:
                escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                where.append("(msg LIKE ? ESCAPE '\\' OR program LIKE ? ESCAPE '\\')")
                args.extend([f"%{escaped}%"] * 2)
        if before_id is not None:
            where.append("id < ?")
            args.append(before_id)
        if after_id is not None:
            where.append("id > ?")
            args.append(after_id)
        sql = (
            "SELECT id, ts, device, source, program, level, msg, verified FROM lines"
            f" WHERE {' AND '.join(where)} ORDER BY id {'ASC' if after_id is not None else 'DESC'} LIMIT ?"
        )
        args.append(max(1, min(limit, 2000)))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = [
            {
                "id": r[0],
                "ts": r[1],
                "device": r[2],
                "source": r[3],
                "program": r[4],
                "level": r[5],
                "msg": r[6],
                "verified": bool(r[7]),
            }
            for r in rows
        ]
        out.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
        return out

    def matching(self, since: float, likes: list[str], limit: int = 20000) -> list[dict[str, Any]]:
        """The newest matching lines since `since`, returned oldest first for sign-in readers."""
        if not likes:
            return []
        cond = " OR ".join("msg LIKE ?" for _ in likes)
        sql = (
            "SELECT id, ts, device, source, program, level, msg, verified FROM lines"
            f" WHERE ts >= ? AND ({cond}) ORDER BY ts DESC, id DESC LIMIT ?"
        )
        with self._lock:
            rows = self._db.execute(sql, [since, *likes, max(1, min(limit, 100000))]).fetchall()
        keys = ("id", "ts", "device", "source", "program", "level", "msg", "verified")
        return [dict(zip(keys, r, strict=True)) for r in reversed(rows)]

    def facets(self, since: float) -> dict[str, Any]:
        """What there is to filter by (lines since `since`): devices, sources, programs."""
        with self._lock:
            devices = self._db.execute(
                "SELECT device, COUNT(*) FROM lines WHERE ts >= ? GROUP BY device", (since,)
            ).fetchall()
            sources = self._db.execute(
                "SELECT source, COUNT(*) FROM lines WHERE ts >= ? GROUP BY source", (since,)
            ).fetchall()
            programs = self._db.execute(
                "SELECT program, COUNT(*) FROM lines WHERE ts >= ? GROUP BY program"
                " ORDER BY COUNT(*) DESC LIMIT 60",
                (since,),
            ).fetchall()
            total = self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        return {
            "devices": dict(devices),
            "sources": dict(sources),
            "programs": dict(programs),
            "total": total,
        }

    def get_state(self, key: str) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def newest(self, device: str, sources: tuple[str, ...], now: float | None = None) -> float | None:
        """The time of a device's newest line from these sources that isn't in the future."""
        now = time.time() if now is None else now
        marks = ", ".join("?" * len(sources))
        with self._lock:
            row = self._db.execute(
                f"SELECT MAX(ts) FROM lines WHERE device = ? AND source IN ({marks}) AND ts <= ?",
                (device, *sources, now + AHEAD),
            ).fetchone()
        return row[0] if row else None

    def prune(self, days: int, max_lines: int, now: float | None = None) -> int:
        """Lines older than `days` go, and lines dated in the future (garbage: they would never
        age out and would stay on top), then the oldest beyond `max_lines`."""
        now = time.time() if now is None else now
        with self._lock:
            gone = self._db.execute(
                "DELETE FROM lines WHERE ts < ? OR ts > ?", (now - days * 86400, now + AHEAD)
            ).rowcount
            count = self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
            if count > max_lines:
                gone += self._db.execute(
                    "DELETE FROM lines WHERE id IN (SELECT id FROM lines ORDER BY id LIMIT ?)",
                    (count - max_lines,),
                ).rowcount
        return gone


# --- OpenWrt's logd ---------------------------------------------------------------------------


def from_logd(
    device: str, entries: list[dict[str, Any]], after: dict[str, Any] | None, now: float | None = None
) -> tuple[list[Line], dict[str, Any] | None]:
    """OpenWrt's `log read` entries (msg, priority = facility*8 + level, source 0 kernel / 1
    syslog, time in ms, id) that come after the cursor; and the new cursor. The cursor is the
    newest time and the ids seen at that time (ids start again at a reboot; times don't).
    logd sometimes hands back garbage from its ring buffer (times near 2**63, fragments of
    text): an entry dated more than AHEAD past the hub's clock is skipped, or it would become
    the cursor and every real line after it would count as old."""
    limit = ((time.time() if now is None else now) + AHEAD) * 1000
    last_t = int((after or {}).get("t", 0))
    seen = set((after or {}).get("ids", []))
    new: list[Line] = []
    newest_t, ids_at_newest = last_t, set(seen)
    for e in entries:
        try:
            t = int(e.get("time") or 0)
            ident = int(e.get("id") or 0)
            priority = int(e.get("priority", 6) or 0)
        except (TypeError, ValueError):
            continue
        if not 0 < t <= limit:
            continue
        if t < last_t or (t == last_t and ident in seen):
            continue
        msg = str(e.get("msg") or "")
        kernel = e.get("source", 1) == 0
        program, text = ("kernel", re.sub(r"^\[\s*\d+\.\d+\]\s*", "", msg)) if kernel else split_program(msg)
        new.append(Line(t / 1000, device, "kernel" if kernel else "syslog", program, priority & 7, text))
        if t > newest_t:
            newest_t, ids_at_newest = t, {ident}
        elif t == newest_t:
            ids_at_newest.add(ident)
    if not new:
        return [], after
    return new, {"t": newest_t, "ids": sorted(ids_at_newest)}


# --- The open syslog port ---------------------------------------------------------------------

RFC5424 = re.compile(r"^<(\d{1,3})>1 (\S+) (\S+) (\S+) (\S+) (\S+) (?:-|(?:\[.*?\])+) ?(.*)$", re.S)
RFC3164 = re.compile(r"^<(\d{1,3})>(?:([A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d) (\S+) )?(.*)$", re.S)


def parse_syslog(data: bytes, device: str, now: float | None = None) -> Line | None:
    """One syslog message (RFC 5424 or the older BSD form). The time is the hub's (a device's
    own clock and its time zone are often wrong, and a datagram is seconds old at most)."""
    now = time.time() if now is None else now
    text = data.decode("utf-8", "replace").strip().lstrip("\ufeff")
    if m := RFC5424.match(text):
        pri, _, _, app, _, _, msg = m.groups()
        program = "" if app == "-" else app
        return Line(now, device, "syslog", program[:64], int(pri) & 7, msg.lstrip("\ufeff"), False)
    if m := RFC3164.match(text):
        pri, _, _, rest = m.groups()
        program, msg = split_program(rest)
        return Line(now, device, "syslog", program, int(pri) & 7, msg, False)
    return None


class SyslogReceiver(asyncio.DatagramProtocol):
    """UDP syslog: lines only from addresses of known devices (`who(address)` gives the device
    id), kept unverified. Others are counted, never stored."""

    def __init__(self, who) -> None:
        self.who = who
        self.pending: list[Line] = []
        self.refused = 0
        self.received = 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        device = self.who(addr[0])
        if device is None:
            self.refused += 1
            return
        line = parse_syslog(data[:8192], device)
        if line is not None:
            self.received += 1
            self.pending.append(line)
            if len(self.pending) > 5000:  # the writer can't keep up: the oldest go
                del self.pending[:1000]

    def take(self) -> list[Line]:
        lines, self.pending = self.pending, []
        return lines
