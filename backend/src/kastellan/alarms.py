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
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from . import ups as upsmod
from .config import Check, Config, Kind
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
    switched: dict[str, dict[str, Any]] | None = None,
) -> list[Problem]:
    """The problems now (critical and warning). `unjudged` gets the keys of UPS and certificate
    problems that can't be judged now (the certificate can't be seen: the service or device is
    down, or behind one that is, or no fresh result): their open alarms stay as they are
    (Alarms.carry), as an alarm about a certificate ends when a good one is seen. `switched`: the
    hub's memory of when each UPS switched to and from battery (ups.reading keeps it; NUT tells
    no times): the only state this reads and updates besides its arguments."""
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

    def above(id: str) -> list[str]:
        """What a device needs to be reachable: its parent (the network path) and, for a virtual
        machine, the host it runs on."""
        d = config.devices[id]
        return [p for p in (d.parent, d.runs_on.host if d.runs_on else None) if p in config.devices]

    @cache
    def chain(id: str) -> list[str]:
        """Everything id hangs on, nearest first (parents and VM hosts, theirs too; cycles cut)."""
        out: list[str] = []
        todo = above(id)
        while todo:
            p = todo.pop(0)
            if p != id and p not in out:
                out.append(p)
                todo += above(p)
        return out

    def vm_state(id: str) -> tuple[str, str] | None:
        """A virtual machine's host and its state there, as the host's collector reports it now
        (an old report, or one from before the hub started, says nothing about now)."""
        runs = config.devices[id].runs_on
        st = status.get(runs.host) if runs else None
        fresh = (
            st and st.host_checked and st.host_checked >= started and now - st.host_checked <= upsmod.MAX_AGE
        )
        report = st.host if st and fresh else None
        vm = next((v for v in (report or {}).get("vms") or [] if v.get("name") == runs.vm), None)
        return (runs.host, str(vm.get("state") or "")) if runs and vm else None

    @cache
    def top_down_ancestor(id: str) -> str | None:
        downs = [p for p in [id, *chain(id)] if p in down]
        # With both a network parent and a VM host, breadth-first order isn't ancestry:
        # a parent may be listed before a host that itself depends on that parent.
        # Mutual dependencies (a virtual router and its host) choose one stable alarm,
        # rather than hiding each other's alarms. Ancestors outside that cycle still win.
        roots = [p for p in downs if not any(a in down and p not in chain(a) for a in chain(p))]
        top = min(roots, default=None)
        return top if top != id else None

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

    # UPSes whose power is the trouble now: on battery, low, or back on mains within minutes (what
    # went down in the outage may still be coming up). What they power folds into their alarm.
    readings: dict[str, dict[str, Any]] = {}
    for id, device in config.devices.items():
        r = upsmod.reading(config, status, id, switched) if device.kind is Kind.UPS else None
        if r and r.get("checked") and r["checked"] >= started and now - r["checked"] <= upsmod.MAX_AGE:
            readings[id] = r  # only from a fresh report
        if device.ups and unjudged is not None and not is_muted(id):
            if id not in readings:
                unjudged.update(f"ups:{id}:{kind}" for kind in ("battery", "replace", "contact"))
            elif {"no_contact", "unknown"} & set(r["flags"]):
                unjudged.update(f"ups:{id}:{kind}" for kind in ("battery", "replace"))

    def power_trouble(id: str, down_since: datetime) -> bool:
        """The UPS is on battery, or back on mains within minutes, and the device went down in
        that outage (not before it: an alarm already open stays its own). A muted UPS folds
        nothing: its devices alarm as usual."""
        r = readings.get(id)
        if r is None or "no_contact" in r["flags"] or is_muted(id):
            return False
        back = upsmod.since(r.get("off_battery_since"))
        trouble = bool({"on_battery", "low_battery"} & set(r["flags"])) or (
            back is not None and timedelta(0) <= now - back < timedelta(minutes=10)
        )
        start = upsmod.since(r.get("on_battery_since"))
        # The host's clock and the hub's may differ a little.
        return trouble and (start is None or down_since >= start - timedelta(minutes=2))

    def troubled_ups(id: str, down_since: datetime) -> str | None:
        """The UPS (up its powered_by chain) whose power trouble a device is down in."""
        seen, at = {id}, config.devices[id].powered_by
        while at and at not in seen and at in config.devices:
            if power_trouble(at, down_since):
                return at
            seen.add(at)
            at = config.devices[at].powered_by
        return None

    problems: list[Problem] = []
    hidden: dict[str, list[str]] = {}
    on_battery: dict[str, list[str]] = {}  # a UPS in trouble -> the devices down with it
    for id in down:
        top = top_down_ancestor(id)
        if u := troubled_ups(top or id, down[top or id]):
            on_battery.setdefault(u, []).append(id)
        elif top:
            hidden.setdefault(top, []).append(id)
    for id, since in down.items():
        if top_down_ancestor(id) or troubled_ups(id, since) or is_muted(id) or parent_settling(id):
            continue
        label = config.label(id)
        behind = sorted(hidden.get(id, []))
        title = f"{label} is down"
        if behind:
            title += f", and {len(behind)} {'device' if len(behind) == 1 else 'devices'} behind it"
        detail = ""
        if (vm := vm_state(id)) and vm[1] != "running":
            detail = f"its virtual machine on {config.label(vm[0])} is {vm[1] or 'in an unknown state'}"
        problems.append(Problem(f"down:{id}", Severity.CRITICAL, id, label, title, detail, since, behind))

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
                name = f"{config.label(id)}: {check.name}"
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
        name = config.label(id)
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
            problems += host_problems(id, name, st.host or {}, limits, st.host_forecast)
        except Exception:  # one host's odd report never stops the alarms of all the others
            log.exception("%s: judging its report failed", id)

    # UPSes: on battery (critical at the limits), a battery to replace, no contact.
    for id, r in readings.items():
        if is_muted(id):
            continue
        name, flags = config.label(id), r["flags"]
        powers = [config.devices[d].name for d in upsmod.powered(config, id)]
        if "no_contact" in flags:
            via = config.devices[id].ups.via  # type: ignore[union-attr]
            problems.append(
                Problem(
                    f"ups:{id}:contact",
                    Severity.WARNING,
                    id,
                    name,
                    f"{name}: its host can't reach it",
                    f"read through {config.label(via)}",
                )
            )
            continue
        if "replace_battery" in flags:
            problems.append(
                Problem(
                    f"ups:{id}:replace",
                    Severity.WARNING,
                    id,
                    name,
                    f"{name}: replace its battery",
                    f"battery from {r['battery_date']}" if r.get("battery_date") else "",
                )
            )
        charge, runtime = r.get("charge"), r.get("runtime")
        if {"on_battery", "low_battery"} & set(flags):
            low = (
                "low_battery" in flags
                or (charge is not None and charge < limits.ups_charge_critical)
                or (runtime is not None and runtime < limits.ups_runtime_critical * 60)
            )
            left = ", ".join(
                p
                for p in (
                    f"{charge:.0f} %" if charge is not None else "",
                    f"{runtime / 60:.0f} min left" if runtime is not None else "",
                )
                if p
            )
            detail = "; ".join(p for p in (left, f"on battery: {', '.join(powers)}" if powers else "") if p)
            problems.append(
                Problem(
                    f"ups:{id}:battery",
                    Severity.CRITICAL if low else Severity.WARNING,
                    id,
                    name,
                    f"{name} is on battery" + (", running low" if low else ""),
                    detail,
                    upsmod.since(r.get("on_battery_since")),
                    sorted(on_battery.get(id, [])),
                )
            )
        elif on_battery.get(id):  # back on mains, and what it powers isn't back yet
            still = sorted(on_battery[id])
            devices = f"{len(still)} {'device' if len(still) == 1 else 'devices'}"
            problems.append(
                Problem(
                    f"ups:{id}:battery",
                    Severity.WARNING,
                    id,
                    name,
                    f"{name} is back on mains; {devices} it powers still down",
                    "",
                    upsmod.since(r.get("off_battery_since")),
                    still,
                )
            )

    # OpenWrt devices: their API answers, and their firmware still gets fixes.
    for id, device in config.devices.items():
        st = status.get(id)
        if device.openwrt is None or st is None or id in down or is_muted(id) or top_down_ancestor(id):
            continue
        name = config.label(id)
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
            cert, name = c.cert, f"{config.label(id)}: {check.name}"
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


def host_problems(
    id: str, name: str, report: dict[str, Any], limits: Any, forecast: dict[str, float] | None = None
) -> list[Problem]:
    """Problems in one collector report: full disks (or full soon, by `forecast`: days left per
    mount, disks.py), RAID, disk health, heat, failed services, VMs that should run but don't."""
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
        elif (days := (forecast or {}).get(mount)) is not None and days < limits.disk_days:
            soon = "within a day" if days < 1 else f"in about {days:.0f} days"
            add(
                f"disk:{mount}",
                Severity.WARNING,
                mount,
                f"{mount} will be full {soon}",
                f"{percent:.0f}% full, {free}",
            )

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
    # A VM set to start with the host is meant to run: one that doesn't is worth a look.
    for vm in report.get("vms") or []:
        if vm.get("autostart") and vm.get("state") not in ("running", None):
            vm_name = str(vm.get("name"))[:80]
            add(f"vm:{vm_name}", Severity.WARNING, f"VM {vm_name}", f"VM {vm_name} is {vm.get('state')}")
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
    held: bool = False  # opened during maintenance: not sent (yet)
    reminded: str | None = None  # when it was last said again (Alarms.update's `remind`)


def _body(p: Problem, after_maintenance: bool = False) -> str:
    body = p.detail or (f"Unreachable behind it: {', '.join(p.hidden)}" if p.hidden else "")
    return f"{body}{' · ' if body else ''}still so after maintenance" if after_maintenance else body


def _hours(d: timedelta) -> str:
    h = round(d.total_seconds() / 3600)
    return f"{h // 24} d {h % 24} h" if h >= 48 else f"{h} h"


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
        # Maintenance started from the app: device → {until, reason, by} (maintenance.py).
        self.maintenance: dict[str, dict[str, Any]] = {}
        self.seen_events: dict[str, str] = {}  # info events already sent: key → when
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            self.open = {k: Alarm(**v) for k, v in data.get("open", {}).items()}
            self.muted = {k: datetime.fromisoformat(v) for k, v in data.get("muted", {}).items()}
            self.maintenance = data.get("maintenance", {})
            seen = data.get("seen_events", {})
            self.seen_events = seen if isinstance(seen, dict) else {k: "" for k in seen}
        except (OSError, ValueError, TypeError) as e:
            if self.path.exists():
                log.warning("cannot read %s (%s); starting without open alarms", self.path, e)

    def save(self) -> None:
        data = {
            "open": {k: asdict(a) for k, a in self.open.items()},
            "muted": {k: v.isoformat() for k, v in self.muted.items()},
            "maintenance": self.maintenance,
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

    def update(
        self,
        problems: list[Problem],
        now: datetime,
        quiet: set[str] | None = None,
        held: set[str] | None = None,
        remind: timedelta | None = None,
    ) -> list[Message]:
        """Open new alarms, update changed ones, resolve those whose problem has been gone for
        RESOLVE_AFTER seconds. `quiet`: devices whose alarms resolve without a message (muted,
        removed). `held`: devices in maintenance: their alarms open without a message, which is
        sent when the maintenance ends with the problem still there (one that ends before is
        never heard of). `remind`: an alarm nobody acknowledged is said again this often (not
        info, not while held or muted). Returns what to send."""
        messages: list[Message] = []
        quiet = quiet or set()
        held = held or set()
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
                    held=p.device in held,
                )
                self.open[key] = alarm
                self._log({"time": alarm.opened, "what": "opened", **asdict(alarm)})
                if not alarm.held:
                    messages.append(Message(p.severity, p.title, _body(p), p.device))
                changed = True
            else:
                if p.device in held and not alarm.held:
                    alarm.held = True
                    changed = True
                if alarm.held and p.device not in held:
                    # The maintenance is over and it is still wrong: now it is news.
                    alarm.held = False
                    alarm.reminded = now.isoformat(timespec="seconds")  # reminders count from now
                    messages.append(Message(p.severity, p.title, _body(p, after_maintenance=True), p.device))
                    self._log(
                        {"time": now.isoformat(timespec="seconds"), "what": "released", **asdict(alarm)}
                    )
                    changed = True
                elif p.severity.rank > Severity(alarm.severity).rank and not alarm.held:
                    # Worse than when it opened (a CVE became known as exploited): say so again.
                    alarm.severity = p.severity.value
                    messages.append(Message(p.severity, p.title, p.detail, p.device))
                    self._log(
                        {"time": now.isoformat(timespec="seconds"), "what": "escalated", **asdict(alarm)}
                    )
                    changed = True
                # Milder now (a disk cleaned up below critical), or new words: kept without a message.
                fresh = (p.severity.value, p.title, p.hidden, p.detail, None)
                if (alarm.severity, alarm.title, alarm.hidden, alarm.detail, alarm.gone_since) != fresh:
                    alarm.severity, alarm.title, alarm.hidden, alarm.detail, alarm.gone_since = fresh
                    changed = True
                if remind and self._due(alarm, now, remind) and p.device not in quiet | held:
                    alarm.reminded = now.isoformat(timespec="seconds")
                    hours = _hours(now - datetime.fromisoformat(alarm.since or alarm.opened))
                    body = f"for {hours}, not acknowledged" + (f" · {p.detail}" if p.detail else "")
                    messages.append(Message(p.severity, f"Still: {p.title}", body, p.device))
                    self._log({"time": alarm.reminded, "what": "reminded", **asdict(alarm)})
                    changed = True
        for key, alarm in list(self.open.items()):
            if key in current:
                continue
            silent = key in folded or alarm.device in quiet | held or alarm.held
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

    @staticmethod
    def _due(alarm: Alarm, now: datetime, every: timedelta) -> bool:
        if alarm.held or alarm.acknowledged_by or alarm.severity == Severity.INFO.value:
            return False
        last = datetime.fromisoformat(alarm.reminded or alarm.opened)
        return now - last >= every

    def start_maintenance(self, device: str, until: datetime | None, reason: str, by: str | None) -> None:
        """Maintenance from the app (until None: ended)."""
        if until is None:
            self.maintenance.pop(device, None)
        else:
            self.maintenance[device] = {"until": until.isoformat(), "reason": reason, "by": by}
        self.save()

    def ended_maintenance(self, now: datetime) -> list[str]:
        """Devices whose maintenance from the app ran out: forgotten, and named for the audit."""
        gone = [d for d, m in self.maintenance.items() if datetime.fromisoformat(m["until"]) <= now]
        if gone:
            for d in gone:
                del self.maintenance[d]
            self.save()
        return gone

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
