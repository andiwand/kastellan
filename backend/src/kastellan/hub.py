"""Ties the parts together: loads config.yaml, reloads it when it changes, runs the monitor."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import config as config_mod
from . import config_edit, demo, docker, notify, vulns
from .access import hub_address
from .alarms import Alarms, Message, Problem, Severity, evaluate
from .audit import Audit
from .auth import Sessions
from .config import Config, Device, NotifyChannel, Role
from .desktop_api import Tickets
from .drivers import DRIVERS
from .history import History
from .host import actor, collector
from .hub_errors import yaml_problem
from .monitor import DeviceStatus, Monitor, run_check
from .networks import Resolver, default_gateway
from .openwrt import client as openwrt_client
from .openwrt.client import Pins, Ubus
from .openwrt.firmware import Releases, advise
from .registry import Registries
from .secrets import Secrets
from .settings import Settings
from .ssh import Keys
from .terminals import Terminals
from .versions import Versions
from .vpn.openvpn import OpenVpn, crl_next_update
from .vuln_policy import assess, auto_update_reason

log = logging.getLogger(__name__)

ROUTER_EVERY = 120  # seconds between reading OpenWrt devices
PACKAGES_EVERY = 6 * 3600  # and asking them for package updates
COLLECT_EVERY = 60  # seconds between the collector's reports
HEARTBEAT_EVERY = 60
RESOLVE_EVERY = 300  # seconds between looking up device names
RELOAD_EVERY = 2  # seconds between looks at config.yaml's mtime
PRUNE_EVERY = 6 * 3600
DRIVER_EVERY = 600
ALARMS_EVERY = 30
VULNS_FIRST = 120
CRL_FIRST = 300  # seconds after start: the first checks and polls have run


class ConfigProblem(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _brief(e: Exception) -> str:
    return (str(e) or type(e).__name__)[:200]


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
        self.secrets = Secrets(settings.data / "secrets")
        # The OpenVPN CRL as last pushed: {"pushed": iso, "next_update": iso, "error": str}
        self.crl_state: dict[str, Any] = {}
        self.terminals = Terminals()
        self.tickets = Tickets()  # one-time remote desktop tickets (desktop_api)
        self.alarms = Alarms(data)
        self._events_since = datetime.now(UTC)
        self.started = datetime.now(UTC)  # alarms use only results from after this
        # (channel, message, first try, last try, tries) for notifications that failed
        self._outbox: list[tuple[NotifyChannel, Message, datetime, datetime, int]] = []
        self.vuln_sources = vulns.Sources(data / "vulns")
        self._container_seen: dict[tuple[str, str], tuple[str, int]] = {}
        runner = self.demo.run if self.demo else run_check
        self.monitor = Monitor(self.history, runner, self.versions)
        self._mtime: float | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._loaded = False  # a config has been loaded since the start
        self.locked: str | None = None  # why the API refuses everything (fail closed), or None
        self.signin_required = settings.data / "signin-required"
        self.resolver = Resolver()
        self.registries = Registries()
        self._background: set[asyncio.Task[None]] = set()  # automatic updates under way
        self.pins = Pins(settings.data / "openwrt")
        self.releases = Releases()
        self._ubus: dict[tuple[str, str, int, str], Ubus] = {}
        self.heartbeat_ok: datetime | None = None
        self.heartbeat_error: str | None = None
        self.own_address: str | None = None  # the hub's address on its network
        self.gateway: str | None = None  # its default gateway: on the boundary to the internet
        self.own_v6: str | None = None  # its IPv6 address, if it has one

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
            try:
                self._mark_signin_required()
            except ConfigProblem as problem:
                self.config_error = problem.message
                self.locked = problem.message
                log.error("%s; the API stays locked", problem.message)
                return
        old_users = self.config.users
        self.config, self.config_error, self.locked, self._loaded = config, None, None, True
        # Someone removed from config.yaml is signed out everywhere at once, and a terminal stays
        # open only for an admin whose password did not change.
        self.sessions.end_all_except(set(config.users))
        for name in set(old_users) - set(config.users):
            self.tickets.drop(user=name)
        self.terminals.stop_all_but(
            {
                name
                for name, user in config.users.items()
                if user.role == Role.ADMIN
                and (name not in old_users or old_users[name].password == user.password)
            },
            "your access changed",
        )
        self._apply(config)
        log.info("config: %d devices, %d users", len(config.devices), len(config.users))

    def check(self, text: str) -> Config:
        """A candidate config.yaml (from the app's editor), by the same rules as loading it, or
        ConfigProblem: what is wrong (with the line, for YAML)."""
        try:
            data = YAML(typ="safe").load(text) if text.strip() else {}
            config = Config.model_validate(data or {})
        except ValidationError as e:
            raise ConfigProblem(_describe(e)) from e
        except YAMLError as e:
            raise ConfigProblem(yaml_problem(e)) from e
        except ValueError as e:
            raise ConfigProblem(_first_line(str(e))) from e
        if not config.users and self._signin_was_on():
            raise ConfigProblem("it has no users, but sign-in is on; that would lock everyone out")
        return config

    def save_config(self, old: str, new: str) -> None:
        """Write config.yaml (checked by check() first), keep the old one, and load it now."""
        if self.demo:
            raise ConfigProblem("demo mode shows a made-up network; its config can't be changed")
        config_edit.write(self.settings.config, self.settings.data / "config-backups", old, new)
        self.reload()

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
        except YAMLError as e:
            raise ConfigProblem(yaml_problem(e)) from e
        except (OSError, ValueError) as e:
            raise ConfigProblem(_first_line(str(e))) from e
        if not config.users and self._signin_was_on():
            raise ConfigProblem(
                "it has no users, but sign-in was on; to turn sign-in off, also delete "
                f"{self.signin_required}"
            )
        return config

    def _signin_was_on(self) -> bool:
        """Whether the marker exists; when it can't be looked at, assume it does (fail closed)."""
        try:
            self.signin_required.stat()
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return True

    def _mark_signin_required(self) -> None:
        """Without the marker, a later config.yaml without users would turn sign-in off; if it
        can't be written, that protection is missing, so the hub refuses to run instead."""
        if self._signin_was_on():
            return
        try:
            self.signin_required.parent.mkdir(parents=True, exist_ok=True)
            self.signin_required.write_text(
                "Sign-in has been on. While this file exists, Kastellan refuses a config.yaml "
                "without users instead of opening up.\n"
            )
        except OSError as e:
            raise ConfigProblem(
                f"cannot write {self.signin_required} ({e.strerror}); fix the data folder"
            ) from e

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
            asyncio.create_task(self._poll_drivers(), name="poll drivers"),
            asyncio.create_task(self._watch_config(), name="watch config"),
            asyncio.create_task(self._expire_tickets(), name="expire tickets"),
            asyncio.create_task(self._watch_alarms(), name="alarms"),
            asyncio.create_task(self._watch_vulns(), name="vulnerabilities"),
            asyncio.create_task(self._renew_crl(), name="renew the OpenVPN CRL"),
            asyncio.create_task(self._prune(), name="prune history"),
            asyncio.create_task(self._resolve_hosts(), name="resolve device names"),
            asyncio.create_task(self._poll_hosts(), name="collect from hosts"),
            asyncio.create_task(self._heartbeat(), name="heartbeat"),
            asyncio.create_task(self._poll_routers(), name="OpenWrt devices"),
        ]

    async def stop(self) -> None:
        for task in [*self._tasks, *self._background]:
            task.cancel()
        for task in list(self._background):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.monitor.stop()
        if self._demo_dir:
            shutil.rmtree(self._demo_dir, ignore_errors=True)

    async def _resolve_hosts(self) -> None:
        """Device names to addresses (for which network each is in), and the hub's own address,
        every few minutes; soon after a config change too."""
        seen: frozenset[str] = frozenset()
        last = 0.0
        while True:
            hosts = frozenset(d.host for d in self.config.devices.values())
            now = asyncio.get_running_loop().time()
            if hosts != seen or now - last > RESOLVE_EVERY:
                if self.demo:  # the made-up network has made-up addresses
                    self.resolver.addresses = demo.addresses(set(hosts))
                    self.own_address, self.gateway = demo.HUB_ADDRESS, demo.GATEWAY
                else:
                    # Each on its own: one failing doesn't keep the others from being known.
                    try:
                        await self.resolver.refresh(set(hosts))
                    except Exception:
                        log.exception("resolving device names failed")
                    self.own_address = await asyncio.to_thread(hub_address, "1.1.1.1", 53)
                    self.own_v6 = await asyncio.to_thread(hub_address, "2606:4700:4700::1111", 53)
                    self.gateway = default_gateway()
                seen, last = hosts, now
            await asyncio.sleep(5)

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

    async def _poll_drivers(self) -> None:
        """Ask each device with a driver or a Docker source what it is and runs, every
        DRIVER_EVERY seconds, a few devices at a time. One device's trouble never stops the
        others; what it said last stays, marked by its time."""
        limit = asyncio.Semaphore(8)

        async def one(id: str, device: Device) -> None:
            async with limit:
                try:
                    # With a collector, it lists the containers (except in the demo, which has none).
                    if device.docker is not None and (device.collect is None or self.demo):
                        await self._poll_docker(id, device.docker.wud)
                    if device.driver is not None:
                        await self._poll_driver(id, device)
                    if device.act is not None and not self.demo:
                        await self._poll_actor(id)
                except Exception:
                    log.exception("asking %s failed", id)

        while True:
            await asyncio.gather(*(one(id, d) for id, d in list(self.config.devices.items())))
            await asyncio.sleep(DRIVER_EVERY)

    async def _poll_hosts(self) -> None:
        """The collector on every device with `collect:`, every COLLECT_EVERY seconds (host
        health; its containers, with updates from the registries)."""
        limit = asyncio.Semaphore(4)

        async def one(id: str, device: Device) -> None:
            async with limit:
                status = self.monitor.status.get(id)
                if status is None:
                    return
                try:
                    report = await collector.collect(self.keys, device)
                except Exception as e:  # what it said last stays, marked by its time
                    status.host_error = _brief(e)
                    log.info("%s: the collector: %s", id, status.host_error)
                    return
                status.host, status.host_checked, status.host_error = report, datetime.now(UTC), None
                items = report.get("docker")
                if isinstance(items, list):
                    try:
                        self._set_containers(id, status, await docker.from_collector(items, self.registries))
                        status.containers_error = None
                    except Exception as e:
                        status.containers_error = _brief(e)
                        log.exception("%s: reading its containers failed", id)

        while True:
            if self.demo:  # the made-up network's made-up reports
                for id, report in demo.HOST.items():
                    if status := self.monitor.status.get(id):
                        status.host, status.host_checked = report, datetime.now(UTC)
            else:
                hosts = [(id, d) for id, d in list(self.config.devices.items()) if d.collect]
                # Each host on its own: one host's trouble never stops the others, or the loop.
                await asyncio.gather(*(one(id, d) for id, d in hosts), return_exceptions=True)
            await asyncio.sleep(COLLECT_EVERY)

    # --- OpenWrt devices (openwrt/) ------------------------------------------------------------

    def openwrt_secret(self, id: str) -> str:
        device = self.config.devices[id]
        return (device.openwrt.password_secret if device.openwrt else None) or f"openwrt-{id}"

    def ubus(self, id: str) -> Ubus:
        """The device's API with Kastellan's login (kept between calls: its session lasts)."""
        device = self.config.devices[id]
        assert device.openwrt is not None
        key = (id, device.host, device.openwrt.port, device.openwrt.user)
        client = self._ubus.get(key)
        if client is None:
            password = self.secrets.get(self.openwrt_secret(id))
            client = Ubus(id, device.host, device.openwrt.port, device.openwrt.user, password, self.pins)
            self._ubus = {k: v for k, v in self._ubus.items() if k[0] != id} | {key: client}
        return client

    async def _poll_routers(self) -> None:
        """Every OpenWrt device's state every ROUTER_EVERY seconds; its package updates and the
        firmware advice now and then."""
        limit = asyncio.Semaphore(4)

        async def one(id: str, device: Device) -> None:
            async with limit:
                status = self.monitor.status.get(id)
                if status is None:
                    return
                try:
                    ubus = self.ubus(id)
                    report = await openwrt_client.read(ubus)
                    old = status.router or {}
                    report["packages"] = old.get("packages")
                    report["packages_checked"] = old.get("packages_checked")
                    now = time.time()
                    if not report["packages_checked"] or now - report["packages_checked"] > PACKAGES_EVERY:
                        try:
                            report["packages"] = await openwrt_client.upgradable(ubus)
                            report["packages_checked"] = now
                        except Exception as e:  # asked again only after the same while
                            report["errors"]["packages"] = _brief(e)
                            report["packages_checked"] = now
                    versions = await self.releases.get()
                    release = (report["board"].get("release") or {}).get("version") or ""
                    advice = advise(release, versions) if versions else None
                    report["firmware"] = advice.view() if advice else None
                except Exception as e:  # what it said last stays, marked by its time
                    status.router_error = _brief(e)
                    log.info("%s: %s", id, status.router_error)
                    return
                status.router, status.router_checked, status.router_error = report, datetime.now(UTC), None
                board = report["board"]
                release = board.get("release") or {}
                firmware = " ".join(x for x in (release.get("version"), release.get("revision")) if x)
                status.info = {
                    "model": board.get("model"),
                    "name": board.get("hostname"),
                    "firmware": firmware or None,
                }
                status.info_checked = datetime.now(UTC)
                if firmware:
                    self._note_firmware(id, f"OpenWrt {firmware}", datetime.now(UTC))

        while True:
            if self.demo:  # the made-up network's made-up access points
                for id, report in demo.ROUTER.items():
                    if status := self.monitor.status.get(id):
                        status.router, status.router_checked = report, datetime.now(UTC)
            else:
                routers = [(id, d) for id, d in list(self.config.devices.items()) if d.openwrt]
                await asyncio.gather(*(one(id, d) for id, d in routers), return_exceptions=True)
            await asyncio.sleep(ROUTER_EVERY)

    async def _heartbeat(self) -> None:
        """Call the heartbeat URL every minute while the hub runs (a dead man's switch)."""
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                url = self.config.heartbeat
                if url and not self.demo:
                    try:
                        (await client.get(url)).raise_for_status()
                        self.heartbeat_ok = datetime.now(UTC)
                        self.heartbeat_error = None
                    except Exception as e:
                        self.heartbeat_error = type(e).__name__
                        log.info("heartbeat failed: %s", self.heartbeat_error)
                await asyncio.sleep(HEARTBEAT_EVERY)

    async def _poll_driver(self, id: str, device: Device) -> None:
        status = self.monitor.status.get(id)
        if status is None or device.driver is None:
            return
        if self.demo:
            if id in demo.INFO:
                status.info, status.info_checked = demo.INFO[id], datetime.now(UTC)
            return
        info = await DRIVERS[device.driver](device.host).info()
        if info is None:
            return
        now = datetime.now(UTC)
        status.info, status.info_checked = asdict(info), now
        if info.firmware:
            self._note_firmware(id, info.firmware, now)

    async def _poll_actor(self, id: str) -> None:
        """Which of the actor's scripts on the host are older than the hub's (for Updates)."""
        status, device = self.monitor.status.get(id), self.config.devices.get(id)
        if status is None or device is None:
            return
        try:
            status.actor_outdated = (await actor.ask(self.keys, device))[1]
        except Exception as e:  # it doesn't answer now: what it said last stays
            log.info("%s: asking the actor failed: %s", id, _brief(e))

    async def _poll_docker(self, id: str, wud: str) -> None:
        status = self.monitor.status.get(id)
        if status is None:
            return
        if self.demo:
            status.containers, status.containers_checked = demo.CONTAINERS.get(id), datetime.now(UTC)
            return
        try:
            containers = await docker.read_wud(wud)
        except Exception as e:
            # Keep what we knew; the app shows since when it's old.
            reason = e or type(e).__name__
            status.containers_error = f"cannot read WUD at {wud} (docker: in config.yaml): {reason}"
            log.info("%s: %s", id, status.containers_error)
            return
        self._set_containers(id, status, containers)

    def _set_containers(self, id: str, status: DeviceStatus, containers: list[docker.Container]) -> None:
        """What a device runs now (from WUD or the collector): stored, and changes logged."""
        now = datetime.now(UTC)
        status.containers, status.containers_checked, status.containers_error = (
            docker.as_dicts(containers),
            now,
            None,
        )
        latest = self.versions.latest()
        for c in containers:
            version = c.running_version
            if version is None:
                continue
            key = f"@container {c.key}"
            last = latest.get((id, key))
            if last is not None and last[0] == version:
                self._container_seen.pop((id, key), None)
                continue
            # Seen twice in a row before it counts, like services' software (monitor.py).
            seen, count = self._container_seen.get((id, key), (version, 0))
            count = count + 1 if seen == version else 1
            if last is not None and count < 2:
                self._container_seen[(id, key)] = (version, count)
                continue
            self._container_seen.pop((id, key), None)
            self.versions.record(now, id, key, last[0] if last else None, version)

    def _note_firmware(self, id: str, firmware: str, now: datetime) -> None:
        last = self.versions.latest().get((id, "@firmware"))
        if last is None or last[0] != firmware:
            if last is not None:
                log.info("%s firmware: %s → %s", id, last[0], firmware)
            self.versions.record(now, id, "@firmware", last[0] if last else None, firmware)

    async def _watch_alarms(self) -> None:
        """Turn what is known into alarms and info events, and send them (alarms.py, notify.py).
        What can't be delivered is retried with growing pauses, for up to a day."""
        first = True
        while True:
            await asyncio.sleep(ALARMS_EVERY)
            try:
                now = datetime.now(UTC)
                unjudged: set[str] = set()
                problems = evaluate(
                    self.config, self.monitor.status, now, self.alarms.muted, self.started, unjudged
                )
                problems += self._vuln_problems()
                problems += self.alarms.carry(self._vuln_keys_unknown())
                problems += self.alarms.carry(unjudged)
                # Muted or removed devices: their alarms end without a "back up".
                quiet = {d for d, until in self.alarms.muted.items() if until > now}
                quiet |= {a.device for a in self.alarms.open.values() if a.device not in self.config.devices}
                messages = self.alarms.update(problems, now, quiet) + self._events(now, send=not first)
                first = False
                await self._send(messages, now)
                await self._retry_outbox(now)
            except Exception:
                log.exception("evaluating the alarms failed")

    async def _send(self, messages: list[Message], now: datetime) -> None:
        """Send, and keep what failed for _retry_outbox."""
        failed = await notify.send(self.config.notify, messages, now)
        self._outbox.extend((c, m, now, now, 1) for c, m in failed)

    async def _retry_outbox(self, now: datetime) -> None:
        """Retry failed notifications: after 1, 2, 4 ... 32 minutes from the last try, for a day;
        all due ones at once, within one time limit (a dead ntfy must not hold up the alarms)."""
        due, later = [], []
        for channel, message, first_try, last_try, tries in self._outbox:
            if (now - first_try).total_seconds() > 86400:
                log.warning("giving up on a notification after a day: %s", message.title)
            elif channel not in self.config.notify:
                continue  # the channel was removed from config.yaml
            elif (now - last_try).total_seconds() >= ALARMS_EVERY * 2 ** min(tries, 6):
                due.append((channel, message, first_try, tries))
            else:
                later.append((channel, message, first_try, last_try, tries))
        self._outbox = later
        if not due:
            return

        async def one(channel: NotifyChannel, message: Message) -> bool:
            return not await notify.send([channel], [message], now)

        try:
            async with asyncio.timeout(20):
                results = await asyncio.gather(*(one(c, m) for c, m, _, _ in due))
        except TimeoutError:
            results = [False] * len(due)
        for (channel, message, first_try, tries), ok in zip(due, results, strict=True):
            if not ok:
                self._outbox.append((channel, message, first_try, now, tries + 1))

    def _vuln_keys_unknown(self) -> set[str]:
        """Open vulnerability alarms whose ground isn't known right now: the device hasn't been
        swept since the hub started, or its product couldn't be looked up. They stay as they are
        (no "Resolved" after every restart, no re-alarm after the next sweep)."""
        keep = set()
        for key, alarm in self.alarms.open.items():
            if not key.startswith("vuln:"):
                continue
            status = self.monitor.status.get(alarm.device)
            vulns_now = status.vulns if status else None
            if vulns_now is None:
                keep.add(key)
                continue
            where = key.split(":", 2)[2].rsplit(":", 1)[0]
            if where in {u.get("where") for u in vulns_now.get("unknown", []) if isinstance(u, dict)}:
                keep.add(key)
        return keep

    def _events(self, now: datetime, send: bool = True) -> list[Message]:
        """Info events: container updates that appeared, software and firmware that changed.
        On the first look after starting, they are only remembered: they aren't news."""
        out: list[Message] = []
        for id, status in self.monitor.status.items():
            device = self.config.devices.get(id)
            if device is None:
                continue
            for c in status.containers or []:
                if c.get("update"):
                    target = c.get("update_to") or "a newer image"
                    key = f"update:{id}:{c.get('key', c['name'])}:{target}"
                    title = f"{device.name}: {c['name']} can be updated to {target}"
                    if m := self.alarms.event(key, title, f"{c['image']}:{c['tag']}", id, now, send):
                        out.append(m)
            # An OpenWrt device's newer point release (within its series): once per release.
            fw = (status.router or {}).get("firmware") or {}
            if fw.get("newest_in_series"):
                key = f"openwrt:{id}:{fw['newest_in_series']}"
                title = (
                    f"{device.name}: OpenWrt {fw['newest_in_series']} is out (it runs {fw.get('current')})"
                )
                if m := self.alarms.event(key, title, "a bug-fix release of its series", id, now, send):
                    out.append(m)
            # The host wants a reboot (kernel or libc updates): once per set of packages.
            updates = (status.host or {}).get("updates") or {}  # normalized (host/report.py)
            if updates.get("reboot"):
                why = ", ".join(str(p) for p in (updates.get("reboot_for") or [])[:6])
                key = f"reboot:{id}:{why}"
                if m := self.alarms.event(
                    key, f"{device.name} wants a reboot", why or "after updates", id, now, send
                ):
                    out.append(m)
            # New findings worth a message: one message per device, not one per finding.
            new: list[str] = []
            for item in (status.vulns or {}).get("items", []):
                for f in item.get("findings", []):
                    if not assess(device, f, item["where"]).message:
                        continue
                    key = f"vuln:{id}:{item['where']}:{f['id']}"
                    is_new = key not in self.alarms.seen_events
                    self.alarms.event(key, "", "", id, now, send=False)  # remember, or keep remembering
                    if is_new and send:
                        note = " (maybe fixed by the distribution)" if f.get("confidence") != "exact" else ""
                        new.append(f"{f['id']} in {item['product']}{note}")
            if new:
                noun = "vulnerability" if len(new) == 1 else "vulnerabilities"
                title = f"{device.name}: {len(new)} new known {noun}"
                out.append(Message(Severity.INFO, title, "; ".join(new[:8]), id))
        # A minute of overlap: the log keeps whole seconds; seen_events keeps it from repeating.
        for change in self.versions.changes(self._events_since - timedelta(seconds=60)):
            device = self.config.devices.get(change["device"])
            name = device.name if device else change["device"]
            key = f"version:{change['device']}:{change['check']}:{change['to']}"
            what = change["check"].removeprefix("@")
            title = f"{name}: {what} updated"
            if m := self.alarms.event(
                key, title, f"{change['from']} → {change['to']}", change["device"], now, send
            ):
                out.append(m)
        self._events_since = now
        return out

    def _vuln_problems(self) -> list[Problem]:
        """Alarms from known vulnerabilities, by vuln_policy.py's table."""
        out: list[Problem] = []
        for id, status in self.monitor.status.items():
            device = self.config.devices.get(id)
            if device is None or not status.vulns:
                continue
            for item in status.vulns.get("items", []):
                for f in item.get("findings", []):
                    severity = assess(device, f, item["where"]).alarm
                    if severity is None:
                        continue
                    what = "exploited " if f.get("exploited") else ""
                    title = f"{device.name}: {what}{f['id']} in {item['product']}"
                    facing = ", internet-facing" if device.faces_internet(item["where"]) else ""
                    detail = f"{f.get('severity')}{facing}: {f.get('summary', '')[:200]}"
                    out.append(
                        Problem(f"vuln:{id}:{item['where']}:{f['id']}", severity, id, title, title, detail)
                    )
        return out

    async def _watch_vulns(self) -> None:
        """Look up what every device runs in the vulnerability sources (vulns.py), shortly after
        starting (once the first checks and polls have run) and then every few hours."""
        await asyncio.sleep(VULNS_FIRST)
        while True:
            settings = self.config.vulns
            if settings.enabled and not self.demo:
                try:
                    await self._sweep_vulns()
                except Exception:
                    log.exception("the vulnerability sweep failed")
            await asyncio.sleep(settings.every_hours * 3600)

    async def _sweep_vulns(self) -> None:
        sources = self.vuln_sources
        sources.nvd_api_key = self.config.vulns.nvd_api_key
        async with httpx.AsyncClient(headers={"User-Agent": "kastellan"}) as client:
            await sources.refresh_kev(client)
            for id, device in list(self.config.devices.items()):
                status = self.monitor.status.get(id)
                if status is None:
                    continue
                try:
                    await self._sweep_device(client, id, device, status)
                except Exception:
                    log.exception("the vulnerability sweep of %s failed", id)
            sources.flush()

    async def _sweep_device(
        self, client: httpx.AsyncClient, id: str, device: Device, status: DeviceStatus
    ) -> None:
        sources = self.vuln_sources
        items, unknown, errors = [], [], {}
        for where, product in self._products(status):
            sources.errors.clear()
            found = await sources.lookup(client, product)
            errors |= sources.errors
            if found is None:
                unknown.append({"where": where, "product": product.label})
                continue
            items.append({"where": where, "product": product.label, "findings": [asdict(f) for f in found]})
        status.vulns = {
            "checked": datetime.now(UTC).isoformat(timespec="seconds"),
            "items": items,
            "unknown": unknown,  # couldn't be asked: not "no vulnerabilities"
            "not_looked_up": self._not_looked_up(status),  # no source knows them (or no exact version)
            "errors": errors,
            "kev": sources.kev_error,
        }
        await self._auto_update(id, device, status)

    def _not_looked_up(self, status: DeviceStatus) -> list[str]:
        """What runs here but can't be looked up: a known image on a moving tag (latest, 7.4)."""
        out = []
        for c in status.containers or []:
            if vulns.image_known(c["image"]) and vulns.from_image(c["image"], c["tag"]) is None:
                out.append(f"{c['image']}:{c['tag']}")
        return out

    def _products(self, status: DeviceStatus) -> list[tuple[str, vulns.Product]]:
        """What a device runs that a vulnerability source knows: services' software, firmware,
        container images."""
        out: list[tuple[str, vulns.Product]] = []
        for name, check in status.checks.items():
            product = vulns.from_software(*vulns.parse_software(check.software)) if check.software else None
            if product:
                out.append((f"service {name}", product))
        firmware = (status.info or {}).get("firmware")
        if firmware and (product := vulns.from_software(*vulns.parse_software(firmware))):
            out.append(("firmware", product))
        for c in status.containers or []:
            if product := vulns.from_image(c["image"], c["tag"]):
                out.append((f"container {c.get('key', c['name'])}", product))
        return out

    async def _auto_update(self, id: str, device: Device, status: DeviceStatus) -> None:
        """Update a container by itself only where vuln_policy.auto_update_reason allows it, and,
        through the actor, only containers on the host's own list. Each runs in the background:
        a long pull doesn't hold up the sweep."""
        via_wud = bool(device.docker and device.docker.trigger)
        if not ((via_wud or device.act) and device.auto_update):
            return
        findings = {item["where"]: item["findings"] for item in (status.vulns or {}).get("items", [])}
        allowed: list[str] | None = None
        for c in status.containers or []:
            key = c.get("key", c["name"])
            reason = auto_update_reason(device, c, findings.get(f"container {key}", []))
            if reason is None:
                continue
            if device.act is not None:
                # The actor pulls the container's own tag again, never another one: a fix in a
                # newer version tag needs that tag in its settings, a person's decision.
                if c.get("update_to") != "newer build":
                    continue
                if allowed is None:
                    try:
                        allowed = (await actor.abilities(self.keys, device)).get("update-container", [])
                    except Exception:
                        allowed = []
                if "*" not in allowed and c["name"] not in allowed:
                    continue  # the host doesn't let Kastellan update this one
            done = f"auto:{id}:{key}:{c.get('update_to')}:{c.get('update_digest', '')}"
            if done in self.alarms.seen_events:
                continue  # once per new image
            self.alarms.event(done, "", "", id, datetime.now(UTC), send=False)
            task = asyncio.create_task(self._update_one(id, device, c, reason), name=f"update {id} {key}")
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _update_one(self, id: str, device: Device, c: dict[str, Any], reason: str) -> None:
        key = c.get("key", c["name"])
        outcome, severity = "interrupted (the hub stopped)", Severity.CRITICAL
        try:
            if device.act is not None:
                result = await actor.act(self.keys, device, "update-container", c["name"])
                outcome = "done" if result.get("updated") else f"nothing to do: {result.get('why', '')}"
            else:
                assert device.docker is not None and device.docker.trigger is not None
                await docker.run_trigger(device.docker.wud, c["wud_id"], device.docker.trigger)
                outcome = "started"
            severity = Severity.WARNING
        except Exception as e:
            outcome, severity = f"failed: {str(e)[:120] or type(e).__name__}", Severity.CRITICAL
        finally:
            # Written whatever happened, also when the hub stops halfway (before any await).
            self.audit.record(
                "update.auto",
                None,
                None,
                device=id,
                container=key,
                to=c.get("update_to"),
                reason=reason,
                outcome=outcome,
            )
        title = f"{device.name}: updating {c['name']} to {c.get('update_to')} ({outcome})"
        # Through the outbox, like every alarm: retried when a channel can't be reached.
        await self._send([Message(severity, title, f"because of {reason}", id)], datetime.now(UTC))

    async def push_crl(self, ovpn: OpenVpn, renew: bool = False) -> None:
        """Bring the CRL to the OpenVPN server and note when it expires."""
        try:
            pem = await ovpn.push_crl(renew)
        except Exception as e:
            self.crl_state = {**self.crl_state, "error": f"{type(e).__name__}: {e}"}
            raise
        self.crl_state = {
            "pushed": datetime.now(UTC).isoformat(timespec="seconds"),
            "next_update": crl_next_update(pem),
            "error": None,
        }

    async def _renew_crl(self) -> None:
        """Renew and push the CRL daily: easy-rsa's CRLs last 180 days, and OpenVPN refuses
        every client once its CRL has expired."""
        await asyncio.sleep(CRL_FIRST)
        while True:
            ovpn = self.config.vpn.openvpn
            if ovpn and ovpn.server and not self.demo:
                devices = self.config.devices
                client = OpenVpn(self.keys, ovpn, devices[ovpn.pki], devices[ovpn.server])
                try:
                    await self.push_crl(client, renew=True)
                    self.audit.record("vpn.openvpn.crl", None, None, outcome="renewed daily")
                except Exception as e:
                    log.warning("renewing the OpenVPN CRL failed: %s", e)
                    await self._send(
                        [Message(Severity.WARNING, "OpenVPN: the CRL couldn't be renewed", str(e)[:200])],
                        datetime.now(UTC),
                    )
            await asyncio.sleep(86400)

    async def _expire_tickets(self) -> None:
        """A ticket holds a desktop password until used; unused ones go after their minute."""
        while True:
            await asyncio.sleep(15)
            self.tickets.expire()

    async def _prune(self) -> None:
        while True:
            try:
                for path in self.history.prune(self.config.retention_days):
                    log.info("history: deleted %s", path.name)
            except OSError as e:
                log.warning("cannot prune the history: %s", e)
            await asyncio.sleep(PRUNE_EVERY)
