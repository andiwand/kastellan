"""Runs every device's checks on their interval, keeps the latest status and writes the history."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from . import checks
from .config import Check, Config, Device
from .history import History, Row

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


@dataclass
class DeviceStatus:
    checks: dict[str, CheckStatus] = field(default_factory=dict)

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


class Monitor:
    def __init__(self, history: History, runner: Runner = run_check) -> None:
        self.history = history
        self.runner = runner
        self.status: dict[str, DeviceStatus] = {}
        self._tasks: dict[tuple[str, str], tuple[asyncio.Task[None], Device, Check, float]] = {}

    def apply(self, config: Config) -> None:
        """Start, keep or stop check loops so they match the config. Unchanged checks keep running."""
        wanted: dict[tuple[str, str], tuple[Device, Check, float]] = {}
        for id, device in config.devices.items():
            for check in device.checks:
                wanted[(id, check.name)] = (device, check, check.interval or config.check_interval)

        for key, (task, device, check, interval) in list(self._tasks.items()):
            if wanted.get(key) != (device, check, interval):
                task.cancel()
                del self._tasks[key]

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
        for id, device in config.devices.items():
            status = self.status.setdefault(id, DeviceStatus())
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
            if check.up is not None and check.up != row.up:
                check.since = row.time
            # The first row of the window: the state began before it, at an unknown time
            # (since stays None: "longer than the history shows").
            check.up, check.latency_ms, check.detail, check.checked = (
                row.up,
                row.latency_ms,
                row.detail,
                row.time,
            )

    async def stop(self) -> None:
        tasks = [t for t, *_ in self._tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop(self, id: str, device: Device, check: Check, interval: float) -> None:
        while True:
            result = await self.runner(id, device, check)
            self._record(id, check.name, result)
            await asyncio.sleep(interval)

    def _record(self, id: str, name: str, result: checks.Result) -> None:
        now = datetime.now(UTC)
        status = self.status.setdefault(id, DeviceStatus()).checks.setdefault(name, CheckStatus())
        if status.up != result.up:
            if status.up is not None:
                log.info("%s %s is %s (%s)", id, name, "up" if result.up else "down", result.detail)
            status.since = now
        status.up, status.latency_ms, status.detail, status.checked = (
            result.up,
            result.latency_ms,
            result.detail,
            now,
        )
        try:
            self.history.append(Row(now, id, name, result.up, result.latency_ms, result.detail))
        except OSError as e:
            log.warning("cannot write the history: %s", e)
