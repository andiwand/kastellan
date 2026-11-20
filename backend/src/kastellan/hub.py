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
from .audit import Audit
from .auth import Sessions
from .config import Config
from .history import History
from .monitor import Monitor, run_check
from .settings import Settings
from .ssh import Keys
from .versions import Versions

log = logging.getLogger(__name__)

RELOAD_EVERY = 2  # seconds between looks at config.yaml's mtime
PRUNE_EVERY = 6 * 3600


class ConfigProblem(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0][:300] if text.strip() else "unreadable"


def _describe(e: ValidationError) -> str:
    """What is wrong, without the values: config.yaml holds password hashes, and a pasted plain
    password must not end up in a log or an API answer."""
    problems = []
    for err in e.errors(include_input=False, include_url=False):
        where = ".".join(str(p) for p in err["loc"])
        problems.append(f"{where}: {err['msg']}" if where else err["msg"])
    return "; ".join(problems)[:600]


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
        # Sign-in state lives next to the real data even in demo mode: it is not made up.
        self.sessions = Sessions(settings.data / "sessions.json")
        self.audit = Audit(settings.data / "audit.log")
        self.keys = Keys(settings.data / "ssh")
        runner = self.demo.run if self.demo else run_check
        self.monitor = Monitor(self.history, runner, self.versions)
        self._mtime: float | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._loaded = False  # a config has been loaded since the start
        self.locked: str | None = None  # why the API refuses everything (fail closed), or None
        self.signin_required = settings.data / "signin-required"

    def reload(self) -> None:
        """Read config.yaml. An invalid file keeps the last good config and reports the error.

        Sign-in fails closed: once it has been on (the marker <data>/signin-required), a missing,
        empty, invalid or user-less config.yaml never turns it off by itself; the hub keeps the
        last good config, or, at start, locks the API until config.yaml is fixed. To turn sign-in
        off on purpose, remove the users and delete the marker."""
        try:
            config = self._load()
        except ConfigProblem as problem:
            self.config_error = problem.message
            if self._loaded:
                log.error("config.yaml: %s; keeping the last good one", problem.message)
            else:
                self.locked = f"config.yaml: {problem.message}"
                log.error("config.yaml: %s; the API stays locked until it is fixed", problem.message)
            return
        if config.users:
            self._mark_signin_required()
        self.config, self.config_error, self.locked, self._loaded = config, None, None, True
        # Someone removed from config.yaml is signed out everywhere at once.
        self.sessions.end_all_except(set(config.users))
        self._apply(config)
        log.info("config: %d devices, %d users", len(config.devices), len(config.users))

    def _load(self) -> Config:
        """The config to use now, or ConfigProblem."""
        path = self.settings.config
        try:
            stat = path.stat() if path.exists() else None
            self._mtime = stat.st_mtime if stat else None
            if self.demo:
                # The made-up network, with the users of the real config.yaml (if it has any).
                users = config_mod.load(path).users
                config = demo.config().model_copy(update={"users": users})
            else:
                if stat is not None and stat.st_size == 0 and self._loaded:
                    raise ConfigProblem("the file is empty (maybe being saved)")
                if stat is None and self._loaded:
                    raise ConfigProblem("the file is missing")
                config = config_mod.load(path)
        except ValidationError as e:
            raise ConfigProblem(_describe(e)) from e
        except (OSError, ValueError, YAMLError) as e:
            raise ConfigProblem(_first_line(str(e))) from e
        if not config.users and self.signin_required.exists():
            raise ConfigProblem(
                "it has no users, but sign-in was on; to turn sign-in off, also delete "
                f"{self.signin_required}"
            )
        return config

    def _mark_signin_required(self) -> None:
        if not self.signin_required.exists():
            try:
                self.signin_required.parent.mkdir(parents=True, exist_ok=True)
                self.signin_required.write_text(
                    "Sign-in has been on. While this file exists, Kastellan refuses a config.yaml "
                    "without users instead of opening up.\n"
                )
            except OSError as e:
                log.warning("cannot write %s: %s", self.signin_required, e)

    def _apply(self, config: Config) -> None:
        first = not self.monitor.status
        if first:
            # Before the first checks run: the last day's history says what each check was doing.
            self.monitor.prepare(config)
            try:
                self.monitor.restore(self.history.read(datetime.now(UTC) - timedelta(days=1)))
                self.versions.compact(max(config.retention_days, 365))
                self.monitor.restore_software(self.versions.latest(), config)
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
