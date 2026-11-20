"""Ties the parts together: loads config.yaml, reloads it when it changes, runs the monitor."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml.error import YAMLError

from . import config as config_mod
from . import demo
from .config import Config
from .history import History
from .monitor import Monitor, run_check
from .settings import Settings
from .versions import Versions

log = logging.getLogger(__name__)

RELOAD_EVERY = 2  # seconds between looks at config.yaml's mtime
PRUNE_EVERY = 6 * 3600


class Hub:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.config = Config()
        self.config_error: str | None = None
        self.demo = demo.Demo() if settings.demo else None
        data = settings.data
        if self.demo:
            # The made-up history never lands next to a real one.
            data = Path(tempfile.mkdtemp(prefix="kastellan-demo-"))
        self._demo_dir = data if self.demo else None
        self.history = History(data / "checks")
        self.versions = Versions(data / "versions.log")
        runner = self.demo.run if self.demo else run_check
        self.monitor = Monitor(self.history, runner, self.versions)
        self._mtime: float | None = None
        self._tasks: list[asyncio.Task[None]] = []

    def reload(self) -> None:
        """Read config.yaml; an invalid file keeps the last good config and reports the error."""
        if self.demo:
            self.config = demo.config()
            self._apply(self.config)
            return
        path = self.settings.config
        try:
            stat = path.stat() if path.exists() else None
            if stat is not None and stat.st_size == 0 and self.config.devices:
                # Most likely an editor between truncating and writing; look again shortly.
                return
            self._mtime = stat.st_mtime if stat else None
            config = config_mod.load(path)
        except (OSError, ValueError, ValidationError, YAMLError) as e:
            self.config_error = str(e)
            log.error("config.yaml is invalid, keeping the last good one: %s", e)
            return
        self.config, self.config_error = config, None
        self._apply(config)
        log.info("config: %d devices", len(config.devices))

    def _apply(self, config: Config) -> None:
        first = not self.monitor.status
        if first:
            # Before the first checks run: the last day's history says what each check was doing.
            self.monitor.prepare(config)
            try:
                self.monitor.restore(self.history.read(datetime.now(UTC) - timedelta(days=1)))
                self.monitor.restore_software(self.versions.latest())
            except Exception:
                log.exception("cannot restore the state from the history; starting fresh")
        self.monitor.apply(config)

    async def start(self) -> None:
        if self.demo:
            log.info("demo mode: a made-up network, simulated results; config.yaml is not read")
            await asyncio.to_thread(self.demo.backfill, self.history, demo.config(), self.versions)
        self.reload()
        self._tasks = [
            asyncio.create_task(self._watch_config(), name="watch config"),
            asyncio.create_task(self._prune(), name="prune history"),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.monitor.stop()
        if self._demo_dir:
            shutil.rmtree(self._demo_dir, ignore_errors=True)

    async def _watch_config(self) -> None:
        while True:
            await asyncio.sleep(RELOAD_EVERY)
            if self.demo:
                continue
            path = self.settings.config
            try:
                mtime = path.stat().st_mtime if path.exists() else None
            except OSError:
                continue
            if mtime != self._mtime:
                try:
                    self.reload()
                except Exception:
                    # Never stop watching: the next save of the file gets another chance.
                    log.exception("reloading config.yaml failed")

    async def _prune(self) -> None:
        while True:
            try:
                for path in self.history.prune(self.config.retention_days):
                    log.info("history: deleted %s", path.name)
            except OSError as e:
                log.warning("cannot prune the history: %s", e)
            await asyncio.sleep(PRUNE_EVERY)
