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


# --- The day's activity, as a person would tell it ----------------------------------------------
#
# Raw ups and downs are noise: a ping lost on Wi-Fi, a laptop opened and closed, every device
# "down" at once while the hub itself had no network. `activity` tells the day instead:
# - an outage: down, and back after how long (or still down), for devices that should be on;
#   drops shorter than the alarm delay are counted per check ("3 short drops"), not listed;
# - many devices unreachable at the same moment: one entry (the hub or the network in front of
#   it had the problem, not each device);
# - devices that may be off (laptops, phones): when they were online.

TOGETHER_WINDOW = timedelta(seconds=90)  # starts this close count as "at once"


@dataclass
class _Span:
    device: str
    check: str
    start: datetime | None  # None: before the window
    end: datetime | None  # None: still so
    detail: str = ""


def _spans(rows: Iterable[Row], want_up: bool) -> dict[tuple[str, str], list[_Span]]:
    """Per check, the stretches it was up (want_up) or down, from its changes in the rows."""
    state: dict[tuple[str, str], bool] = {}
    open_: dict[tuple[str, str], _Span] = {}
    out: dict[tuple[str, str], list[_Span]] = {}
    for row in rows:
        key = (row.device, row.check)
        first = key not in state
        if not first and state[key] == row.up:
            continue
        state[key] = row.up
        if row.up == want_up:
            open_[key] = _Span(row.device, row.check, None if first else row.time, None, row.detail)
        elif key in open_:
            span = open_.pop(key)
            span.end = row.time
            out.setdefault(key, []).append(span)
    for key, span in open_.items():
        out.setdefault(key, []).append(span)
    return out


def activity(
    rows: list[Row],
    always_on: dict[str, bool],
    now: datetime,
    short: timedelta,
    gap: timedelta,
) -> list[dict[str, object]]:
    """The day's activity (see above), newest first. `always_on`: per device; `short`: drops up to
    this long are only counted; `gap`: offline stretches up to this long don't split an online
    one."""
    out: list[dict[str, object]] = []
    downs = [s for spans in _spans(rows, want_up=False).values() for s in spans if s.start is not None]

    # Many devices unreachable at once: one entry.
    downs.sort(key=lambda s: s.start or now)
    devices_total = len({r.device for r in rows}) or 1
    needed = max(4, round(devices_total * 0.3))
    together: set[int] = set()
    i = 0
    while i < len(downs):
        start = downs[i].start
        assert start is not None
        j = i
        while j + 1 < len(downs) and (downs[j + 1].start or now) - start <= TOGETHER_WINDOW:
            j += 1
        group = downs[i : j + 1]
        names = sorted({s.device for s in group})
        if len(names) >= needed:
            ends = [s.end for s in group]
            end = None if any(e is None for e in ends) else max(e for e in ends if e is not None)
            out.append(
                {"kind": "together", "start": start, "end": end, "devices": names, "count": len(names)}
            )
            together.update(id(s) for s in group)
            i = j + 1
        else:
            i += 1

    # What starts while many are unreachable is part of that, not news of its own.
    windows = [(e["start"], e["end"] or now) for e in out if e["kind"] == "together"]

    def during(t: datetime | None) -> bool:
        return t is not None and any(a <= t <= b + TOGETHER_WINDOW for a, b in windows)  # type: ignore[operator]

    # Outages and short drops of devices that should be on.
    blips: dict[tuple[str, str], list[_Span]] = {}
    for s in downs:
        if id(s) in together or during(s.start) or not always_on.get(s.device, True):
            continue
        length = (s.end or now) - (s.start or now)
        if s.end is not None and length < short:
            blips.setdefault((s.device, s.check), []).append(s)
        elif s.end is not None or length >= short:
            out.append(
                {
                    "kind": "outage",
                    "device": s.device,
                    "check": s.check,
                    "start": s.start,
                    "end": s.end,
                    "detail": s.detail,
                }
            )
    for (device, check), spans in blips.items():
        out.append(
            {
                "kind": "blips",
                "device": device,
                "check": check,
                "count": len(spans),
                "last": max(s.end for s in spans if s.end),
            }
        )

    # When devices that may be off were online (their ping, else their first check).
    checks_of: dict[str, list[str]] = {}
    for r in rows:
        if r.check not in checks_of.setdefault(r.device, []):
            checks_of[r.device].append(r.check)
    ups = _spans(rows, want_up=True)
    for device, checks in checks_of.items():
        if always_on.get(device, True):
            continue
        check = "ping" if "ping" in checks else checks[0]
        merged: list[_Span] = []
        for s in ups.get((device, check), []):
            last_end = merged[-1].end if merged else None
            if (
                last_end is not None
                and s.start is not None
                and (s.start - last_end <= gap or during(last_end))  # short, or the hub's trouble
            ):
                merged[-1].end = s.end  # a short drop doesn't split it
            else:
                merged.append(_Span(s.device, s.check, s.start, s.end))
        for s in merged:
            if s.start is None and s.end is None:
                continue  # online the whole time: nothing happened
            out.append({"kind": "online", "device": device, "start": s.start, "end": s.end})

    def when(e: dict[str, object]) -> datetime:
        """Newest first: by when it ended; what is still so on top."""
        value = e["last"] if e["kind"] == "blips" else e.get("end")
        return value if isinstance(value, datetime) else now

    out.sort(key=when, reverse=True)
    return out
