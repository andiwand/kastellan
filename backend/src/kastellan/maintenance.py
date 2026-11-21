"""Maintenance: planned work (config.yaml `maintenance:`) and work started from the app ("for the
next hour"). A device in maintenance keeps showing its problems, marked; their alarms are held:
sent only if still there when it ends (alarms.Alarms.update). Times are the hub's local time,
like quiet hours (notify.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from .config import DAYS, Config, MaintenanceWindow


@dataclass
class Active:
    until: datetime
    reason: str
    planned: bool  # from config.yaml (else started from the app, and can be ended there)


def covered(config: Config, w: MaintenanceWindow) -> list[str]:
    """The devices a window is for: its devices, its groups' and those at its sites."""
    ids = set(w.devices)
    for g in w.groups:
        ids.update(config.groups[g].devices if g in config.groups else ())
    ids.update(d for d in config.devices if config.site_of(d) in w.sites)
    return sorted(ids & set(config.devices))


def times(w: MaintenanceWindow, now: datetime) -> tuple[datetime, datetime] | None:
    """The window's current (else next) start and end, if there is one now or within a week."""
    if w.start is not None and w.end is not None:
        return (w.start, w.end) if w.end > now else None
    assert w.at and w.minutes
    hour, minute = map(int, w.at.split(":"))
    today = now.astimezone().date()
    length = timedelta(minutes=w.minutes)
    # From as far back as one could still be running, to a week ahead.
    current = None
    for offset in range(-(length.days + 1), 8):
        day = today + timedelta(days=offset)
        if DAYS[day.weekday()] not in w.days:
            continue
        start = datetime.combine(day, time(hour, minute)).astimezone()  # local, with its offset
        if start <= now < start + length:
            current = (start, start + length)  # latest active occurrence ends last
        elif start > now:
            return current or (start, start + length)
    return current


def active(config: Config, now: datetime, adhoc: dict[str, dict[str, Any]]) -> dict[str, Active]:
    """The devices in maintenance now, each until when (the latest of its windows) and why."""
    out: dict[str, Active] = {}

    def add(id: str, a: Active) -> None:
        if id not in out or a.until > out[id].until:
            out[id] = a

    for w in config.maintenance:
        t = times(w, now)
        if t and t[0] <= now:
            for id in covered(config, w):
                add(id, Active(t[1], w.name or "planned maintenance", True))
    for id, m in adhoc.items():
        until = datetime.fromisoformat(m["until"])
        if until > now and id in config.devices:
            add(id, Active(until, m.get("reason") or "maintenance", False))
    return out


def upcoming(config: Config, now: datetime) -> list[dict[str, Any]]:
    """The planned windows with their current or next times, for the app."""
    out = []
    for i, w in enumerate(config.maintenance):
        t = times(w, now)
        out.append(
            {
                "index": i,
                "name": w.name,
                "devices": covered(config, w),
                "start": t[0] if t else None,
                "end": t[1] if t else None,
                "now": bool(t and t[0] <= now),
            }
        )
    return out
