"""Alarms: what the monitor knows, turned into problems a person should hear about.

Rules (defaults in config.yaml's `alarms:`):

- critical: a device that should always be on is down for `down_after` seconds (3 min);
- warning: a service (a non-ping check) of such a device is down that long;
- info: a container has an update, a device's firmware or a service's software changed.

A device that is down hides the devices behind it (its `parent` chain): one alarm says "wifi2 is
down, and 3 devices behind it are unreachable", not four alarms. A muted device raises nothing.
An alarm resolves by itself when the problem is gone (and says so, for critical and warning).

`evaluate` is a pure function of what is known now; `Alarms` keeps the open ones in
<data>/alarms.json and every alarm in <data>/alarms.log, and decides what to send (notify.py).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import Config
from .monitor import DeviceStatus

log = logging.getLogger(__name__)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


@dataclass
class Problem:
    """Something wrong now. `key` identifies it across evaluations."""

    key: str
    severity: Severity
    device: str
    title: str
    detail: str = ""
    since: datetime | None = None
    hidden: list[str] = field(default_factory=list)  # devices behind it, folded into this one


def _down_since(status: DeviceStatus, names: list[str]) -> datetime | None:
    """When all of these checks went down (the latest of their `since`), if all are down."""
    checks = [status.checks.get(n) for n in names]
    if not checks or any(c is None or c.up is not False for c in checks):
        return None
    times = [c.since for c in checks if c and c.since]
    return max(times) if times else None


def evaluate(
    config: Config, status: dict[str, DeviceStatus], now: datetime, muted: dict[str, datetime]
) -> list[Problem]:
    """The problems now (critical and warning; info events come from Alarms.events)."""
    after = timedelta(seconds=config.alarms.down_after)

    def is_muted(id: str) -> bool:
        return id in muted and muted[id] > now

    # Devices that are down (all checks down for long enough, and should be on).
    down: dict[str, datetime] = {}
    for id, device in config.devices.items():
        st = status.get(id)
        if st is None or not device.always_on or is_muted(id):
            continue
        since = _down_since(st, [c.name for c in device.checks])
        if since is not None and now - since >= after:
            down[id] = since

    def ancestor_down(id: str) -> str | None:
        seen: set[str] = set()
        parent = config.devices[id].parent
        while parent and parent not in seen:
            seen.add(parent)
            if parent in down:
                return parent
            parent = config.devices[parent].parent if parent in config.devices else None
        return None

    problems: list[Problem] = []
    hidden: dict[str, list[str]] = {}
    for id in down:
        if top := ancestor_down(id):
            hidden.setdefault(top, []).append(id)
    for id, since in down.items():
        if ancestor_down(id):
            continue
        device = config.devices[id]
        behind = sorted(hidden.get(id, []))
        title = f"{device.name} is down"
        if behind:
            title += f", and {len(behind)} {'device' if len(behind) == 1 else 'devices'} behind it"
        problems.append(Problem(f"down:{id}", Severity.CRITICAL, id, title, "", since, behind))

    # Services down on devices that are otherwise up.
    for id, device in config.devices.items():
        st = status.get(id)
        if st is None or id in down or not device.always_on or is_muted(id) or ancestor_down(id):
            continue
        for check in device.checks:
            if check.type == "ping":
                continue
            c = st.checks.get(check.name)
            if c is not None and c.up is False and c.since and now - c.since >= after:
                problems.append(
                    Problem(
                        f"service:{id}:{check.name}",
                        Severity.WARNING,
                        id,
                        f"{device.name}: {check.name} is down",
                        c.detail,
                        c.since,
                    )
                )
    return problems


@dataclass
class Alarm:
    key: str
    severity: str
    device: str
    title: str
    detail: str
    opened: str  # ISO times, as stored
    since: str | None = None
    hidden: list[str] = field(default_factory=list)
    acknowledged_by: str | None = None
    acknowledged_note: str | None = None
    acknowledged_at: str | None = None


@dataclass
class Message:
    """What notify.py sends."""

    severity: Severity
    title: str
    body: str
    device: str | None = None
    resolved: bool = False


class Alarms:
    def __init__(self, folder: Path) -> None:
        self.path = folder / "alarms.json"
        self.log_path = folder / "alarms.log"
        self.open: dict[str, Alarm] = {}
        self.muted: dict[str, datetime] = {}
        self.seen_events: set[str] = set()  # info events already sent
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.open = {k: Alarm(**v) for k, v in data.get("open", {}).items()}
            self.muted = {k: datetime.fromisoformat(v) for k, v in data.get("muted", {}).items()}
            self.seen_events = set(data.get("seen_events", []))
        except (OSError, ValueError, TypeError) as e:
            if self.path.exists():
                log.warning("cannot read %s (%s); starting without open alarms", self.path, e)

    def save(self) -> None:
        data = {
            "open": {k: asdict(a) for k, a in self.open.items()},
            "muted": {k: v.isoformat() for k, v in self.muted.items()},
            "seen_events": sorted(self.seen_events)[-2000:],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot save the alarms: %s", e)

    def _log(self, what: str, alarm: Alarm, now: datetime, **extra: Any) -> None:
        line = json.dumps({"time": now.isoformat(timespec="seconds"), "what": what, **asdict(alarm), **extra})
        try:
            with self.log_path.open("a") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("cannot write the alarm log: %s", e)

    def update(self, problems: list[Problem], now: datetime) -> list[Message]:
        """Open new alarms, update changed ones, resolve gone ones; returns what to send."""
        messages: list[Message] = []
        current = {p.key: p for p in problems}
        for key, p in current.items():
            alarm = self.open.get(key)
            if alarm is None:
                alarm = Alarm(
                    key,
                    p.severity.value,
                    p.device,
                    p.title,
                    p.detail,
                    now.isoformat(timespec="seconds"),
                    p.since.isoformat(timespec="seconds") if p.since else None,
                    p.hidden,
                )
                self.open[key] = alarm
                self._log("opened", alarm, now)
                body = p.detail or (f"Unreachable behind it: {', '.join(p.hidden)}" if p.hidden else "")
                messages.append(Message(p.severity, p.title, body, p.device))
            elif alarm.title != p.title or alarm.hidden != p.hidden:
                alarm.title, alarm.hidden, alarm.detail = p.title, p.hidden, p.detail  # more behind it
        for key in [k for k in self.open if k not in current and not k.startswith("event:")]:
            alarm = self.open.pop(key)
            self._log("resolved", alarm, now)
            opened = datetime.fromisoformat(alarm.since or alarm.opened)
            minutes = max(1, round((now - opened).total_seconds() / 60))
            messages.append(
                Message(
                    Severity(alarm.severity),
                    alarm.title.replace(" is down", " is back up", 1).split(", and ")[0],
                    f"after {minutes} min",
                    alarm.device,
                    resolved=True,
                )
            )
        if messages:
            self.save()
        return messages

    def event(self, key: str, title: str, body: str, device: str | None, now: datetime) -> Message | None:
        """An info event (an update, a new version), sent once."""
        if key in self.seen_events:
            return None
        self.seen_events.add(key)
        self.save()
        with open(self.log_path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "time": now.isoformat(timespec="seconds"),
                        "what": "event",
                        "key": key,
                        "title": title,
                        "body": body,
                        "device": device,
                    }
                )
                + "\n"
            )
        return Message(Severity.INFO, title, body, device)

    def acknowledge(self, key: str, user: str | None, note: str, now: datetime) -> Alarm | None:
        alarm = self.open.get(key)
        if alarm is None:
            return None
        alarm.acknowledged_by, alarm.acknowledged_note = user or "?", note[:500]
        alarm.acknowledged_at = now.isoformat(timespec="seconds")
        self._log("acknowledged", alarm, now)
        self.save()
        return alarm

    def mute(self, device: str, until: datetime | None) -> None:
        if until is None:
            self.muted.pop(device, None)
        else:
            self.muted[device] = until
        self.save()
