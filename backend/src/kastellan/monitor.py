"""Runs every device's checks on their interval, keeps the latest status and writes the history."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from . import checks, tls
from .config import Check, Config, Device
from .history import History, Row
from .versions import Versions

log = logging.getLogger(__name__)


class State(StrEnum):
    UNKNOWN = "unknown"
    UP = "up"
    PARTIAL = "partial"  # some checks up, some down
    DOWN = "down"


@dataclass
class CheckStatus:
    up: bool | None = None
    latency_ms: float | None = None
    detail: str = ""
    checked: datetime | None = None
    since: datetime | None = None  # when `up` last changed
    # False when `since` is only the oldest result we know of (after a restart): "for at least".
    since_exact: bool = True
    software: str | None = None  # what the service says it is, e.g. "OpenSSH 10.2p1 (Ubuntu)"
    software_since: datetime | None = None  # when we first saw this software
    cert: tls.Cert | None = None  # https: the certificate it presents (tls.py)
    address: str | None = None  # http to a name: the address it led to


@dataclass
class DeviceStatus:
    checks: dict[str, CheckStatus] = field(default_factory=dict)
    info: dict[str, Any] | None = None  # from the device's driver: model, name, firmware, ...
    info_checked: datetime | None = None
    containers: list[dict[str, Any]] | None = None  # from docker.py
    containers_checked: datetime | None = None
    containers_error: str | None = None
    vulns: dict[str, Any] | None = None  # from the vulnerability sweep (hub, vulns.py)

    @property
    def state(self) -> State:
        ups = [c.up for c in self.checks.values() if c.up is not None]
        if not ups:
            return State.UNKNOWN
        if all(ups):
            return State.UP
        return State.DOWN if not any(ups) else State.PARTIAL


Runner = Callable[[str, Device, Check], Awaitable[checks.Result]]


async def run_check(id: str, device: Device, check: Check) -> checks.Result:
    return await checks.run(device.host, check)


def key_changed(old: dict[tuple[str, str], str], key: tuple[str, str], target: str) -> bool:
    return key in old and old[key] != target


def target_of(device: Device, check: Check) -> str:
    """What a check looks at, to tell a reused check name from the same check."""
    return f"{check.type}:{device.host}:{check.target or ''}"


# A new software must be seen this many times in a row before it counts: a page that sometimes
# answers differently (a proxy's error page, a flaky header) must not look like an update.
SOFTWARE_CONFIRM = 2


class Monitor:
    def __init__(
        self, history: History, runner: Runner = run_check, versions: Versions | None = None
    ) -> None:
        self.history = history
        self.runner = runner
        self.versions = versions
        self.status: dict[str, DeviceStatus] = {}
        self._targets: dict[tuple[str, str], str] = {}
        self._urls: dict[tuple[str, str], object] = {}  # each check's target as configured
        self._hosts: dict[str, str] = {}
        self._software_seen: dict[tuple[str, str], tuple[str, int]] = {}
        self._tasks: dict[tuple[str, str], tuple[asyncio.Task[None], Device, Check, float]] = {}

    def apply(self, config: Config) -> None:
        """Start, keep or stop check loops so they match the config. A loop keeps running unless
        its own check, interval or the device's host changed (a new name or tag restarts nothing)."""
        wanted: dict[tuple[str, str], tuple[Device, Check, float]] = {}
        for id, device in config.devices.items():
            for check in device.checks:
                wanted[(id, check.name)] = (device, check, check.interval or config.check_interval)

        def same(a: tuple[Device, Check, float], b: tuple[Device, Check, float]) -> bool:
            return (a[0].host, a[1], a[2]) == (b[0].host, b[1], b[2])

        for key, (task, device, check, interval) in list(self._tasks.items()):
            new = wanted.get(key)
            if new is None or not same(new, (device, check, interval)) or task.done():
                task.cancel()
                del self._tasks[key]
                if new is not None and new[0].host != device.host:
                    # Another address: what we knew about the old one says nothing.
                    self.status.get(key[0], DeviceStatus()).checks.pop(key[1], None)

        for key, (device, check, interval) in wanted.items():
            if key not in self._tasks:
                task = asyncio.create_task(self._loop(key[0], device, check, interval), name=f"check {key}")
                self._tasks[key] = (task, device, check, interval)

        self.prepare(config)

    def prepare(self, config: Config) -> None:
        """Make the status hold exactly the configured devices and checks, keeping what is known."""
        for id in list(self.status):
            if id not in config.devices:
                del self.status[id]
        old = self._targets
        self._targets = {
            (id, c.name): target_of(device, c) for id, device in config.devices.items() for c in device.checks
        }
        self._urls = {(id, c.name): c.target for id, device in config.devices.items() for c in device.checks}
        for (id, name), target in self._targets.items():
            st = self.status.get(id)
            c = st.checks.get(name) if st else None
            if c and key_changed(old, (id, name), target):
                c.cert = c.address = None  # about what it pointed at before
        for id, device in config.devices.items():
            status = self.status.setdefault(id, DeviceStatus())
            if device.docker is None or self._hosts.get(id, device.host) != device.host:
                status.containers = status.containers_checked = status.containers_error = None
            if device.driver is None or self._hosts.get(id, device.host) != device.host:
                status.info = status.info_checked = None
            self._hosts[id] = device.host
            names = {c.name for c in device.checks}
            status.checks = {n: s for n, s in status.checks.items() if n in names}
            for name in names:
                status.checks.setdefault(name, CheckStatus())

    def restore(self, rows: Iterable[Row]) -> None:
        """Take the last known state of each check from the history (rows in time order), so a
        restart does not forget how long something has been up or down."""
        for row in rows:
            status = self.status.get(row.device)
            if status is None or row.check not in status.checks:
                continue
            check = status.checks[row.check]
            if check.up is None:
                # The oldest result we have: the state began then or earlier.
                check.since, check.since_exact = row.time, False
            elif check.up != row.up:
                check.since, check.since_exact = row.time, True
            check.up, check.latency_ms, check.detail, check.checked = (
                row.up,
                row.latency_ms,
                row.detail,
                row.time,
            )

    def restore_software(
        self, latest: dict[tuple[str, str], tuple[str, datetime, str | None]], config: Config
    ) -> None:
        """The software each check last saw, from the version log; not if the check now points
        somewhere else (a reused name)."""
        for (id, name), (software, when, target) in latest.items():
            device = config.devices.get(id)
            check = next((c for c in device.checks if c.name == name), None) if device else None
            status = self.status.get(id, DeviceStatus()).checks.get(name)
            if check is None or status is None:
                continue
            if target is not None and target != target_of(device, check):
                continue
            status.software, status.software_since = software, when

    async def stop(self) -> None:
        tasks = [t for t, *_ in self._tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, id: str, device: Device, check: Check, interval: float) -> None:
        while True:
            try:
                result = await self.runner(id, device, check)
                self._record(id, check.name, result)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A loop must never die quietly; the stale `checked` time shows it in the app.
                log.exception("check %s %s failed", id, check.name)
            await asyncio.sleep(interval)

    def _note_software(self, now: datetime, id: str, name: str, result: checks.Result) -> None:
        status = self.status[id].checks[name]
        key = (id, name)
        if result.software is None or str(result.software) == status.software:
            self._software_seen.pop(key, None)
            return
        new = str(result.software)
        seen, count = self._software_seen.get(key, (new, 0))
        count = count + 1 if seen == new else 1
        if count < SOFTWARE_CONFIRM:
            self._software_seen[key] = (new, count)
            return
        self._software_seen.pop(key, None)
        if status.software is not None:
            log.info("%s %s: %s → %s", id, name, status.software, new)
        if self.versions is not None:
            self.versions.record(now, id, name, status.software, new, self._targets.get(key))
        status.software, status.software_since = new, now

    def _record(self, id: str, name: str, result: checks.Result) -> None:
        now = datetime.now(UTC)
        status = self.status.setdefault(id, DeviceStatus()).checks.setdefault(name, CheckStatus())
        if status.up != result.up:
            if status.up is not None:
                log.info("%s %s is %s (%s)", id, name, "up" if result.up else "down", result.detail)
            status.since, status.since_exact = now, True
        status.up, status.latency_ms, status.detail, status.checked = (
            result.up,
            result.latency_ms,
            result.detail,
            now,
        )
        https = str(self._urls.get((id, name), "")).startswith("https://")
        if result.cert is not None or not result.up or not https:
            # A certificate seen before stays while the service answers but a look at it failed
            # (too slow, once); not when the service is down, or the check isn't https.
            status.cert = result.cert
        status.address = result.address
        self._note_software(now, id, name, result)
        try:
            self.history.append(Row(now, id, name, result.up, result.latency_ms, result.detail))
        except OSError as e:
            log.warning("cannot write the history: %s", e)
