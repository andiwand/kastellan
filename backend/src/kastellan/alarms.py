"""Alarms: what the monitor knows, turned into problems a person should hear about.

Rules (defaults in config.yaml's `alarms:`):

- critical: a device that should always be on is down for `down_after` seconds (3 min);
- warning: a service (a non-ping check) of such a device is down that long;
- certificates of watched https checks (`tls`, by default names on the internet): critical when
  not valid (expired, wrong name, not trusted), warning `cert_days` (14) before they end;
- vulnerabilities: see vuln_policy.py (exploited or critical on an exposed device: critical);
- info: a container has an update, a device's firmware or a service's software changed.

Care is taken not to wake anyone for nothing:

- Only fresh results count: from after the hub started, and not stale (a check that stopped
  reporting is unknown, neither up nor down). No alarm from history just after a restart.
- A device that is down covers everything behind it (its `parent` chain, to the top-most device
  that is down): one alarm. A muted device that is down still covers what is behind it.
- Devices behind a device that just came back wait one check interval before they can alarm
  on their own (their checks may not have run since).
- An alarm resolves only when its problem has been gone for `resolve_after` seconds (90 s), so a
  flapping service doesn't send "down"/"back up" all night.
- Resolving because a device was muted, removed, or folded into its parent's alarm is silent.

`evaluate` is a pure function of what is known now; `Alarms` keeps the open ones in
<data>/alarms.json and every alarm in <data>/alarms.log, and decides what to send (notify.py).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config import Check, Config
from .monitor import CheckStatus, DeviceStatus

log = logging.getLogger(__name__)

RESOLVE_AFTER = 90  # seconds a problem must be gone before its alarm resolves
EVENTS_KEPT_DAYS = 60


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
    name: str  # what it is about, for "... is back up"
    title: str
    detail: str = ""
    since: datetime | None = None
    hidden: list[str] = field(default_factory=list)  # devices behind it, folded into this one


def _fresh(c: CheckStatus | None, check: Check, interval: float, started: datetime, now: datetime) -> bool:
    """A result that says something now: after the hub started, and not overdue."""
    if c is None or c.up is None or c.checked is None or c.checked < started:
        return False
    return (now - c.checked).total_seconds() <= 3 * (check.interval or interval) + 60


def evaluate(
    config: Config,
    status: dict[str, DeviceStatus],
    now: datetime,
    muted: dict[str, datetime],
    started: datetime,
    unjudged: set[str] | None = None,
) -> list[Problem]:
    """The problems now (critical and warning). `unjudged` gets the keys of certificate
    problems that can't be judged now (the certificate can't be seen: the service or device is
    down, or behind one that is, or no fresh result): their open alarms stay as they are
    (Alarms.carry), as an alarm about a certificate ends when a good one is seen."""
    after = timedelta(seconds=config.alarms.down_after)
    interval = config.check_interval

    def is_muted(id: str) -> bool:
        return id in muted and muted[id] > now

    def fresh_checks(id: str) -> list[tuple[Check, CheckStatus]] | None:
        st = status.get(id)
        device = config.devices[id]
        if st is None:
            return None
        pairs = [(c, st.checks.get(c.name)) for c in device.checks]
        if not all(_fresh(s, c, interval, started, now) for c, s in pairs):
            return None
        return [(c, s) for c, s in pairs if s is not None]

    # Devices that are down: every check down, all fresh, long enough; and should be on.
    down: dict[str, datetime] = {}
    for id, device in config.devices.items():
        if not device.always_on:
            continue
        pairs = fresh_checks(id)
        if not pairs or any(s.up for _, s in pairs):
            continue
        since = max((s.since for _, s in pairs if s.since), default=None)
        if since is not None and now - since >= after:
            down[id] = since

    def chain(id: str) -> list[str]:
        """id's parents, nearest first (cycles cut)."""
        out: list[str] = []
        parent = config.devices[id].parent
        while parent and parent in config.devices and parent not in out and parent != id:
            out.append(parent)
            parent = config.devices[parent].parent
        return out

    def top_down_ancestor(id: str) -> str | None:
        downs = [p for p in chain(id) if p in down]
        return downs[-1] if downs else None

    def parent_settling(id: str) -> bool:
        """A parent came back less than a check interval ago: its children's checks may not have
        run since; wait before they alarm on their own."""
        for p in chain(id):
            st = status.get(p)
            if st is None:
                continue
            # Only reachability counts (a flapping service must not hold the children back).
            ping = st.checks.get("ping")
            if ping and ping.up and ping.since and (now - ping.since).total_seconds() < interval + 30:
                return True
        return False

    problems: list[Problem] = []
    hidden: dict[str, list[str]] = {}
    for id in down:
        if top := top_down_ancestor(id):
            hidden.setdefault(top, []).append(id)
    for id, since in down.items():
        if top_down_ancestor(id) or is_muted(id) or parent_settling(id):
            continue
        device = config.devices[id]
        behind = sorted(hidden.get(id, []))
        title = f"{device.name} is down"
        if behind:
            title += f", and {len(behind)} {'device' if len(behind) == 1 else 'devices'} behind it"
        problems.append(Problem(f"down:{id}", Severity.CRITICAL, id, device.name, title, "", since, behind))

    # Services down on devices that are otherwise up (and not behind a device that is down).
    for id, device in config.devices.items():
        if id in down or not device.always_on or is_muted(id) or top_down_ancestor(id) or parent_settling(id):
            continue
        st = status.get(id)
        for check in device.checks:
            if check.type == "ping" or st is None:
                continue
            c = st.checks.get(check.name)
            if check.watch_cert and c and c.cert and not c.cert.valid:
                continue  # down because its certificate isn't valid: that alarm says it
            if (
                _fresh(c, check, interval, started, now)
                and c
                and c.up is False
                and c.since
                and now - c.since >= after
            ):
                name = f"{device.name}: {check.name}"
                problems.append(
                    Problem(
                        f"service:{id}:{check.name}",
                        Severity.WARNING,
                        id,
                        name,
                        f"{name} is down",
                        c.detail,
                        c.since,
                    )
                )

    # Hosts with a collector: what their own report says (host/collector.py).
    limits = config.alarms
    for id, device in config.devices.items():
        st = status.get(id)
        if device.collect is None or st is None or id in down or is_muted(id) or top_down_ancestor(id):
            continue
        name = device.name
        if st.host_checked is None or st.host_checked < started:
            continue  # nothing read since the hub started: no judgment yet
        if (now - st.host_checked).total_seconds() > 15 * 60:
            problems.append(
                Problem(
                    f"host:{id}:collector",
                    Severity.WARNING,
                    id,
                    f"{name}: the collector",
                    f"{name}: the collector stopped answering",
                    st.host_error or "",
                    st.host_checked,
                )
            )
            continue
        try:
            problems += host_problems(id, name, st.host or {}, limits)
        except Exception:  # one host's odd report never stops the alarms of all the others
            log.exception("%s: judging its report failed", id)

    # OpenWrt devices: their API answers, and their firmware still gets fixes.
    for id, device in config.devices.items():
        st = status.get(id)
        if device.openwrt is None or st is None or id in down or is_muted(id) or top_down_ancestor(id):
            continue
        name = device.name
        checked = st.router_checked
        if checked is None or checked < started:
            if st.router_error and (now - started).total_seconds() > 15 * 60:
                problems.append(
                    Problem(
                        f"router:{id}:api",
                        Severity.WARNING,
                        id,
                        f"{name}: its API",
                        f"{name}: Kastellan can't read it",
                        st.router_error,
                    )
                )
            continue
        if (now - checked).total_seconds() > 15 * 60:
            problems.append(
                Problem(
                    f"router:{id}:api",
                    Severity.WARNING,
                    id,
                    f"{name}: its API",
                    f"{name}: Kastellan can't read it any more",
                    st.router_error or "",
                    checked,
                )
            )
            continue
        fw = (st.router or {}).get("firmware") or {}
        if fw and fw.get("supported") is False:
            step = f"; next: {fw['next_step']}" if fw.get("next_step") else ""
            now_out = f"now {fw.get('stable')} and {fw.get('oldstable')}"
            problems.append(
                Problem(
                    f"router:{id}:firmware",
                    Severity.WARNING,
                    id,
                    f"{name}: its firmware",
                    f"{name} runs OpenWrt {fw.get('current')}, which no longer gets security fixes",
                    f"OpenWrt {fw.get('series')} is out of support ({now_out}){step}",
                )
            )

    # Certificates of watched checks.
    for id, device in config.devices.items():
        if is_muted(id):
            continue  # its alarms end quietly (Alarms.update)
        st = status.get(id)
        for check in device.checks:
            if not check.watch_cert:
                continue
            key = f"cert:{id}:{check.name}"
            c = st.checks.get(check.name) if st else None
            if (
                id in down
                or top_down_ancestor(id)
                or not (c and c.cert and _fresh(c, check, interval, started, now))
            ):
                if unjudged is not None:
                    unjudged.add(key)
                continue
            cert, name = c.cert, f"{device.name}: {check.name}"
            days = (cert.expires - now).total_seconds() / 86400 if cert.expires else None
            if not cert.valid:
                severity, title = Severity.CRITICAL, f"{name}: certificate not valid"
                detail = cert.error or ""
            elif days is not None and days < config.alarms.cert_days:
                left = "less than a day" if days < 1 else f"{int(days)} {'day' if int(days) == 1 else 'days'}"
                severity, title = Severity.WARNING, f"{name}: certificate ends in {left}"
                detail = f"{cert.expires:%Y-%m-%d %H:%M} UTC"
                detail += f", from {cert.issuer}" if cert.issuer else ""
            else:
                continue
            # No `since`: how long the service has been up says nothing about the certificate.
            problems.append(Problem(key, severity, id, name, title, detail))
    return problems


def host_problems(id: str, name: str, report: dict[str, Any], limits: Any) -> list[Problem]:
    """Problems in one collector report: full disks, RAID, disk health, heat, failed services."""
    out: list[Problem] = []

    def add(key: str, severity: Severity, what: str, title: str, detail: str = "") -> None:
        out.append(Problem(f"host:{id}:{key}", severity, id, f"{name}: {what}", f"{name}: {title}", detail))

    # What the host couldn't read can't be judged: say so, rather than stay quiet.
    errors = report.get("errors") or {}
    for section in ("filesystems", "raid", "smart"):
        if section in errors:
            add(
                f"unread:{section}",
                Severity.WARNING,
                section,
                f"can't read its {section}",
                str(errors[section])[:200],
            )

    pools: set[str] = set()
    for fs in report.get("filesystems") or []:
        if fs.get("type") == "zfs":  # datasets of one pool share its space: one alarm per pool
            pool = str(fs.get("device", "")).split("/", 1)[0]
            if pool in pools:
                continue
            pools.add(pool)
        try:
            used, avail = int(fs["used"]), int(fs["available"])
            percent = 100 * used / (used + avail) if used + avail else 0
            mount = str(fs["mount"])[:100]
        except (KeyError, TypeError, ValueError):
            continue
        free = f"{avail / 1e9:.1f} GB free" if avail < 1e12 else f"{avail / 1e12:.2f} TB free"
        if percent >= limits.disk_critical:
            add(f"disk:{mount}", Severity.CRITICAL, mount, f"{mount} is {percent:.0f}% full", free)
        elif percent >= limits.disk_warning:
            add(f"disk:{mount}", Severity.WARNING, mount, f"{mount} is {percent:.0f}% full", free)

    for array in report.get("raid") or []:
        if not isinstance(array, dict):
            continue
        md = str(array.get("name", "?"))[:20]
        state = str(array.get("status") or "")
        failed = [str(d) for d in array.get("failed") or []][:8]
        if not array.get("active") or "_" in state or failed:
            detail = f"[{state}]" + (f", failed: {', '.join(failed)}" if failed else "")
            progress = array.get("progress") or {}
            if isinstance(progress, dict) and progress.get("what") == "recovery":
                detail += f", rebuilding {progress.get('percent')}%"
            add(f"raid:{md}", Severity.CRITICAL, md, f"RAID {md} is degraded", detail)

    for disk in (report.get("smart") or {}).get("disks") or []:
        if not isinstance(disk, dict):
            continue
        dev = str(disk.get("disk", "?"))[:20]
        label = f"{dev}" + (f" ({disk['model']})" if disk.get("model") else "")
        if disk.get("passed") is False:
            add(f"smart:{dev}", Severity.CRITICAL, dev, f"disk {label} fails its health check")
            continue
        bad = {
            "reallocated sectors": disk.get("reallocated"),
            "pending sectors": disk.get("pending"),
            "uncorrectable sectors": disk.get("uncorrectable"),
            "media errors": disk.get("nvme_media_errors"),
        }
        signs = [f"{v} {k}" for k, v in bad.items() if isinstance(v, int) and v > 0]
        if isinstance(disk.get("nvme_used_percent"), int) and disk["nvme_used_percent"] >= 90:
            signs.append(f"{disk['nvme_used_percent']}% of its rated wear used")
        if signs:
            add(f"smart:{dev}", Severity.WARNING, dev, f"disk {label} shows wear", ", ".join(signs))
        temp = disk.get("temperature")
        if isinstance(temp, int | float) and temp >= limits.disk_temperature:
            add(f"disktemp:{dev}", Severity.WARNING, dev, f"disk {label} is hot ({temp:.0f} °C)")

    for t in report.get("temperatures") or []:
        if not isinstance(t, dict):
            continue
        value, limit = t.get("celsius"), t.get("limit")
        if isinstance(value, int | float) and isinstance(limit, int | float) and value >= limit:
            sensor = str(t.get("sensor", "?"))[:60]
            add(
                f"temp:{sensor}",
                Severity.WARNING,
                sensor,
                f"{sensor} is at {value:.0f} °C (limit {limit:.0f})",
            )

    failed = [str(u)[:80] for u in (report.get("services") or {}).get("failed") or []]
    if failed:
        noun = "service has" if len(failed) == 1 else "services have"
        add("services", Severity.WARNING, "services", f"{len(failed)} {noun} failed", ", ".join(failed[:10]))
    return out


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
    name: str = ""  # for "... is back up"
    gone_since: str | None = None  # when its problem was last missing (resolves after a while)


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
        self.seen_events: dict[str, str] = {}  # info events already sent: key → when
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.open = {k: Alarm(**v) for k, v in data.get("open", {}).items()}
            self.muted = {k: datetime.fromisoformat(v) for k, v in data.get("muted", {}).items()}
            seen = data.get("seen_events", {})
            self.seen_events = seen if isinstance(seen, dict) else {k: "" for k in seen}
        except (OSError, ValueError, TypeError) as e:
            if self.path.exists():
                log.warning("cannot read %s (%s); starting without open alarms", self.path, e)

    def save(self) -> None:
        data = {
            "open": {k: asdict(a) for k, a in self.open.items()},
            "muted": {k: v.isoformat() for k, v in self.muted.items()},
            "seen_events": self.seen_events,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1))
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("cannot save the alarms: %s", e)

    def _log(self, entry: dict[str, Any]) -> None:
        try:
            with self.log_path.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("cannot write the alarm log: %s", e)

    def update(self, problems: list[Problem], now: datetime, quiet: set[str] | None = None) -> list[Message]:
        """Open new alarms, update changed ones, resolve those whose problem has been gone for
        RESOLVE_AFTER seconds. `quiet`: devices whose alarms resolve without a message (muted,
        removed). Returns what to send."""
        messages: list[Message] = []
        quiet = quiet or set()
        changed = False
        current = {p.key: p for p in problems}
        folded = {f"down:{h}" for p in problems for h in p.hidden}
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
                    name=p.name,
                )
                self.open[key] = alarm
                self._log({"time": alarm.opened, "what": "opened", **asdict(alarm)})
                body = p.detail or (f"Unreachable behind it: {', '.join(p.hidden)}" if p.hidden else "")
                messages.append(Message(p.severity, p.title, body, p.device))
                changed = True
            else:
                if p.severity.rank > Severity(alarm.severity).rank:
                    # Worse than when it opened (a CVE became known as exploited): say so again.
                    alarm.severity = p.severity.value
                    messages.append(Message(p.severity, p.title, p.detail, p.device))
                    self._log(
                        {"time": now.isoformat(timespec="seconds"), "what": "escalated", **asdict(alarm)}
                    )
                    changed = True
                if alarm.gone_since or alarm.title != p.title or alarm.hidden != p.hidden:
                    alarm.title, alarm.hidden, alarm.detail, alarm.gone_since = (
                        p.title,
                        p.hidden,
                        p.detail,
                        None,
                    )
                    changed = True
        for key, alarm in list(self.open.items()):
            if key in current:
                continue
            silent = key in folded or alarm.device in quiet
            if not silent:
                if alarm.gone_since is None:
                    alarm.gone_since = now.isoformat(timespec="seconds")
                    changed = True
                    continue
                if (now - datetime.fromisoformat(alarm.gone_since)).total_seconds() < RESOLVE_AFTER:
                    continue
            del self.open[key]
            changed = True
            self._log(
                {
                    "time": now.isoformat(timespec="seconds"),
                    "what": "resolved",
                    "silent": silent,
                    **asdict(alarm),
                }
            )
            if silent:
                continue
            opened = datetime.fromisoformat(alarm.since or alarm.opened)
            minutes = max(1, round((now - opened).total_seconds() / 60))
            title = (
                f"{alarm.name or alarm.device} is back up"
                if key.startswith(("down:", "service:"))
                else f"{alarm.name}: certificate is fine again"
                if key.startswith("cert:")
                else f"Resolved: {alarm.title}"
            )
            messages.append(
                Message(Severity(alarm.severity), title, f"after {minutes} min", alarm.device, resolved=True)
            )
        if changed:
            self.save()
        return messages

    def carry(self, keys: set[str]) -> list[Problem]:
        """Open alarms to keep as they are, because what decides them isn't known right now (a
        device not swept yet after a restart, a source that couldn't be asked)."""
        out = []
        for key in keys:
            a = self.open.get(key)
            if a is not None:
                since = datetime.fromisoformat(a.since) if a.since else None
                out.append(
                    Problem(key, Severity(a.severity), a.device, a.name, a.title, a.detail, since, a.hidden)
                )
        return out

    def event(
        self, key: str, title: str, body: str, device: str | None, now: datetime, send: bool = True
    ) -> Message | None:
        """An info event (an update, a new version), sent once. With send=False it is only
        remembered (the first look after starting: what was already there isn't news)."""
        if key in self.seen_events:
            self.seen_events[key] = now.isoformat(timespec="seconds")  # still there: keep knowing it
            return None
        self.seen_events[key] = now.isoformat(timespec="seconds")
        cutoff = (now - timedelta(days=EVENTS_KEPT_DAYS)).isoformat()
        self.seen_events = {k: t for k, t in self.seen_events.items() if t >= cutoff or k == key}
        self.save()
        if not send:
            return None
        self._log(
            {
                "time": now.isoformat(timespec="seconds"),
                "what": "event",
                "key": key,
                "title": title,
                "body": body,
                "device": device,
            }
        )
        return Message(Severity.INFO, title, body, device)

    def acknowledge(self, key: str, user: str | None, note: str, now: datetime) -> Alarm | None:
        alarm = self.open.get(key)
        if alarm is None:
            return None
        alarm.acknowledged_by, alarm.acknowledged_note = user or "?", note[:500]
        alarm.acknowledged_at = now.isoformat(timespec="seconds")
        self._log({"time": alarm.acknowledged_at, "what": "acknowledged", **asdict(alarm)})
        self.save()
        return alarm

    def mute(self, device: str, until: datetime | None) -> None:
        if until is None:
            self.muted.pop(device, None)
        else:
            self.muted[device] = until
        self.save()


def stamp(now: datetime | None = None) -> datetime:
    return now or datetime.now(UTC)
