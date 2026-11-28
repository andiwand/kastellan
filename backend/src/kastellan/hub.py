"""Ties the parts together: loads config.yaml, reloads it when it changes, runs the monitor."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

import httpx
from pydantic import ValidationError
from ruamel.yaml.error import YAMLError

from . import (
    __version__,
    config_edit,
    demo,
    demo_more,
    devbackups,
    dhcp,
    discovery,
    docker,
    footprint,
    hubbackup,
    internet_api,
    logs_poll,
    maintenance,
    notify,
    releases,
    roaming,
    signins_api,
    vulns,
    web_setup,
    wifi_api,
)
from . import config as config_mod
from .abilities import Offered
from .access import hub_address
from .alarms import Alarms, Message, Problem, Severity, evaluate
from .audit import Audit
from .auth import Sessions, password_tag
from .backups import Backups
from .config import Config, Device, NotifyChannel, OpenVpnSource, Role
from .desktop_api import Tickets
from .disks import DiskHistory
from .drivers import DRIVERS
from .drivers.fritzbox import firmware_name
from .fritzbox import client as fritz_client
from .fritzbox import web as fritz_web_api
from .fritzbox.client import Fritz
from .history import History
from .host import actor, collector
from .hub_errors import ConfigProblem, describe, first_line, yaml_problem
from .hubbackup import HubBackup
from .internet import Internet
from .logs import LogStore
from .monitor import DeviceStatus, Monitor, run_check
from .networks import Resolver, default_gateway
from .openwrt import client as openwrt_client
from .openwrt import setup as openwrt_setup
from .openwrt.client import Ubus
from .openwrt.firmware import Releases, advise
from .own_services import ServiceStates
from .pinned_http import Pins
from .push import Push
from .registry import NEWER_BUILD, Registries
from .secrets import DEVICE_BACKUPS_KEY, HUB_BACKUP_KEY, Secrets
from .settings import Settings
from .skipped import Skipped
from .ssh import Keys
from .syslog_tls import Authority
from .terminals import Terminals
from .vault import AUTO, KASTELLAN, Damaged, Vault, WrongKey, policy
from .versions import Versions
from .vitals import Busy, Vitals, disks_of, from_fritz, from_host, from_openwrt
from .vitals import Cpu as VitalsCpu
from .vitals import Store as VitalsStore
from .vpn.openvpn import OpenVpn, Seen, crl_next_update, parse_status, server_meta, split_status
from .vuln_policy import assess, auto_update_reason
from .wifi import Scans

log = logging.getLogger(__name__)

ROUTER_EVERY = 120  # seconds between reading OpenWrt devices
PACKAGES_EVERY = 6 * 3600  # and asking them for package updates
COLLECT_EVERY = 60  # seconds between the collector's reports
HEARTBEAT_EVERY = 60
RESOLVE_EVERY = 300  # seconds between looking up device names
RELOAD_EVERY = 2  # seconds between looks at config.yaml's mtime
PRUNE_EVERY = 6 * 3600
DRIVER_EVERY = 600
FRITZ_EVERY = 60
ECO_EVERY = 300  # seconds between reads of a FRITZ!Box's energy monitor (CPU, RAM, temperature)
WG_EVERY = 600  # seconds between reads of a FRITZ!Box's WireGuard connections (web interface)
WG_BACKOFF, WG_BACKOFF_MAX = 1800, 6 * 3600  # after a failure: 30 min, doubling, up to 6 h
ACTIVE_WINDOW = 30 * 60  # a ring that stays open while admins are here: used within this
ALARMS_EVERY = 30
VULNS_FIRST = 120
CRL_FIRST = 300  # seconds after start: the first checks and polls have run
CRL_EVERY = 86400  # seconds between renewing the CRLs (easy-rsa's CRLs last 180 days)
NAMES_LOOK = 5  # seconds between looks whether device names changed (resolved on a change)
RELOCK_EVERY = 30  # seconds between looks whether a ring's policy closes it
TICKETS_EVERY = 15  # seconds between dropping unused desktop tickets
RELEASES_FIRST = 60  # seconds after start before asking GitHub for releases
VPN_HELPERS_FIRST = 120  # seconds after start before asking the VPN scripts their versions
VPN_HELPERS_EVERY = 12 * 3600  # and between asking them again
FRITZ_TIMEOUT = 90  # seconds for one FRITZ!Box's whole read
WEB_TIMEOUT = 10  # seconds for one call to a FRITZ!Box's web interface
HTTP_TIMEOUT = 10  # seconds for one HTTP call (the heartbeat, a web push)
OUTBOX_KEEP = 86400  # seconds a failed notification is retried before it is given up
SEND_TIMEOUT = 20  # seconds for all due retries at once: a dead channel never holds up alarms
OVPN_STALE = 300  # seconds after which an OpenVPN status file says nothing about now


def _log_failure(task: asyncio.Task[Any]) -> None:
    if not task.cancelled() and (e := task.exception()) is not None:
        log.error("%s failed", task.get_name(), exc_info=e)


def _brief(e: Exception) -> str:
    return (str(e) or type(e).__name__)[:200]


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
        self.data = data  # where what Kastellan sees is kept (a temporary folder in demo mode)
        self.history = History(data / "checks")
        self.services = ServiceStates(data / "services")  # devices' own services over time
        self.versions = Versions(data / "versions.log")
        # Sign-in state lives next to the real data even in demo mode: it is not made up.
        self.sessions = Sessions(settings.data / "sessions.json")
        self.audit = Audit(settings.data / "audit.log")
        # The key rings (vault.py): locked at every start, until an admin signs in (or, as the
        # config says, opened by a key from a file, the environment, or config.yaml).
        self.vault = Vault(settings.data / "vault.json")
        self.vault.policy = lambda ring: policy(self.config.vault, ring)
        self.keys = Keys(settings.data / "ssh", self.vault)
        self.secrets = Secrets(settings.data / "secrets", self.vault)
        # Per OpenVPN VPN (its id under vpns:): the CRL as last pushed ({"pushed": iso,
        # "next_update": iso, "error": str}); who is connected to its server (read through the
        # server's OpenWrt login: {"checked": iso, "connected": [...], "error": str | None});
        # when each client was last seen.
        self.crl_state: dict[str, dict[str, Any]] = {}
        self.ovpn_status: dict[str, dict[str, Any]] = {}
        # Every OpenVPN server on the OpenWrt routers (their `openvpn` ability), by device.
        self.vpn_servers: dict[str, dict[str, Any]] = {}
        # Addresses VPNs gave their clients (address -> "client (VPN)"): the Access page's paths.
        self.vpn_addresses: dict[str, dict[str, str]] = {}  # per VPN: client address → its name
        # OpenVPN helper scripts older than this hub's, by VPN ("pki", "crl"): Updates says so.
        self.vpn_outdated: dict[str, list[str]] = {}
        # nlbwmon's bytes per device, by router (the `bandwidth` ability), with rates.
        self.bandwidth: dict[str, dict[str, Any]] = {}
        # usteer's view per access point (the `roaming` ability): wifi.py's roaming.
        self.roaming: dict[str, dict[str, Any]] = {}
        self.discovery_ignored = discovery.Ignored(data / "discovery-ignored.json")
        # Names, certificates and routers' DNS for web.domain (web_setup.py), and when looked.
        self.web_state: dict[str, Any] = {}
        self.web_state_t = 0.0
        # Outside addresses whose first success is new (signins_api.run): told once as an event.
        self.signin_flags: list[dict[str, Any]] = []
        self._seen: dict[str, Seen] = {}
        self.terminals = Terminals()
        self.tickets = Tickets()  # one-time remote desktop tickets (desktop_api)
        self.alarms = Alarms(data)
        self.disks = DiskHistory(data)
        self.resources = footprint.Resources()  # the hub's own CPU, memory and data folder
        self.vitals = VitalsStore(data / "vitals.db")  # CPU, memory, storage over time (vitals.py)
        self._cpu = VitalsCpu()
        self._busy = Busy()  # disks' busy shares, from their I/O time counters
        self._eco_next: dict[str, tuple[float, int]] = {}  # FRITZ!Box energy monitor: next read
        self.backups = Backups(data)
        self.logs = LogStore(data / "logs.db")  # central logs (logs.py)
        self.log_status: dict[str, dict[str, Any]] = {}  # per device: how fetching its log went
        self.syslog = logs_poll.Syslog()  # the open syslog port, when config.yaml turns it on
        self.syslog_tls = logs_poll.SyslogTls()  # syslog over TLS with client certificates
        self.syslog_ca = Authority(data / "syslog", self.own_secret)
        # Device configuration backups (devbackups.py): encrypted with a key of Kastellan's own.
        self.device_backups = devbackups.Store(
            data / "device-backups",
            demo_more.demo_key
            if self.demo
            else lambda: base64.b64decode(self.own_secret(DEVICE_BACKUPS_KEY, devbackups.new_key)),
        )
        self.backup_status: dict[str, dict[str, Any]] = {}  # per device: the last attempt
        self.offered = Offered(data / "abilities-offered.json")  # new abilities passed on
        self.skipped = Skipped(data / "updates-skipped.json")  # container updates passed on
        # Kastellan's own backup (hubbackup.py): its key comes from a passphrase, not the hub.
        self.hub_backup = HubBackup(data if self.demo else settings.data, self.hub_backup_key)
        self.internet = Internet(data / "internet")  # the internet connection (internet.py)
        self.wifi_scans = Scans(data / "wifi")  # what the radios heard (wifi.py)
        self.wifi_scanning: set[str] = set()  # devices scanning now
        self._demo_filled = False  # the demo's week of vitals is in (for the charts)
        if self.demo:
            demo_more.seed(self)
        self.latest_release: releases.Release | None = None  # Kastellan's newest (releases.py)
        self.push = Push(settings.data, self.vault)  # web push: the hub's key, the browsers that allowed it
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
        # When each UPS switched to and from battery, as seen here (NUT tells no times): ups.py.
        self.ups_switched: dict[str, dict[str, Any]] = {}
        self._loaded = False  # a config has been loaded since the start
        self.locked: str | None = None  # why the API refuses everything (fail closed), or None
        # KASTELLAN_HOSTS when it couldn't be written into config.yaml (seed_from_env): this run only.
        self._env_hosts: tuple[str, ...] = ()
        self.signin_required = settings.data / "signin-required"
        self.resolver = Resolver()
        self.registries = Registries()
        # Tasks started from a loop that outlive it (automatic updates): kept until done, and
        # cancelled when the hub stops.
        self._background: set[asyncio.Task[None]] = set()
        self.pins = Pins(settings.data / "openwrt")
        self.fritz_pins = Pins(settings.data / "fritzbox")  # FRITZ!Boxes' confirmed certificates
        self._fritz: dict[tuple[str, str, int, str], Fritz] = {}
        self._fritz_web: dict[str, fritz_web_api.Web] = {}  # per FRITZ!Box: its web interface's session
        self._wg_next: dict[str, tuple[float, int]] = {}  # FRITZ!Box WireGuard: next read, failures
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
            try:
                self.backups.sync(config)
            except OSError as e:
                raise ConfigProblem("could not save the backup configuration") from e
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
        # Vitals of a device read another way (or another device) start over.
        moved: set[str] = set()
        for id, old in self.config.devices.items():
            new = config.devices.get(id)
            if new is None or (old.host, old.collect, old.openwrt, old.fritzbox) != (
                new.host,
                new.collect,
                new.openwrt,
                new.fritzbox,
            ):
                moved.add(id)
                if status := self.monitor.status.get(id):
                    status.vitals = None
            if new is None or (old.host, old.openwrt, old.fritzbox) != (new.host, new.openwrt, new.fritzbox):
                self.drop_clients(id)
        if moved:
            self._cpu.forget(set(config.devices) - moved)
            self._busy.forget(set(config.devices) - moved)
            self.services.forget(set(config.devices) - moved)
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
        if not self.demo:
            self.open_rings_by_themselves()
        log.info("config: %d devices, %d users", len(config.devices), len(config.users))

    def hosts(self) -> tuple[str, ...]:
        """The names the hub answers to (besides IP addresses and localhost): config.yaml's."""
        return tuple(self.config.hosts) or self._env_hosts

    def seed_from_env(self) -> None:
        """What the container's environment says about the hub goes into config.yaml once, at
        the first start that has it; from then on config.yaml is the only source (the Settings
        page changes it there). KASTELLAN_HOSTS → `hosts`. If config.yaml can't be written, the
        variable counts for this run only (else the hub would refuse its own name)."""
        wanted = list(dict.fromkeys(h.strip().lower().rstrip(".") for h in self.settings.hosts if h.strip()))
        marker = self.settings.data / "env-seeded.json"
        if not wanted or self.demo or self.locked or marker.exists():
            return
        old = config_edit.read(self.settings.config)
        if self.config.hosts:  # set in the file already: it wins
            if list(self.config.hosts) != wanted:
                log.warning("KASTELLAN_HOSTS is not used: config.yaml has hosts (change them there)")
            new = old
        else:
            new = config_edit.set_top(old, "hosts", wanted)
        try:
            if new != old:
                self.check(new)
                self.save_config(old, new)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"hosts": wanted}) + "\n")
        except (ConfigProblem, OSError) as e:
            self._env_hosts = self.settings.hosts
            why = e.message if isinstance(e, ConfigProblem) else (e.strerror or type(e).__name__)
            log.error("couldn't write KASTELLAN_HOSTS into config.yaml (%s): using it for this run", why)
            return
        if new != old:
            log.info("config.yaml: hosts taken from KASTELLAN_HOSTS (%s)", ", ".join(wanted))
            self.audit.record("config.from_env", None, None, hosts=wanted)

    def check(self, text: str) -> Config:
        """A candidate config.yaml (from the app's editor), by the same rules as loading it, or
        ConfigProblem: what is wrong (with the line, for YAML)."""
        return self._parsed(
            lambda: config_mod.parse(text),
            "it has no users, but sign-in is on; that would lock everyone out",
        )

    def _parsed(self, read: Callable[[], Config], no_users: str) -> Config:
        """read()'s config, or ConfigProblem: what is wrong, never quoting the file; `no_users`
        when it has no users while sign-in is on (it never turns itself off)."""
        try:
            config = read()
        except ValidationError as e:
            raise ConfigProblem(describe(e)) from e
        except YAMLError as e:
            raise ConfigProblem(yaml_problem(e)) from e
        except (OSError, ValueError) as e:
            raise ConfigProblem(first_line(str(e))) from e
        if not config.users and self._signin_was_on():
            raise ConfigProblem(no_users)
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

        def read() -> Config:
            stat = path.stat() if path.exists() else None
            self._mtime = stat.st_mtime if stat else None
            if self.demo:
                # The made-up network, with the users of the real config.yaml (if it has any).
                users = config_mod.load(path).users
                return demo.config().model_copy(update={"users": users})
            if stat is not None and stat.st_size == 0 and self._loaded:
                raise ConfigProblem("the file is empty (maybe being saved)")
            if stat is None and self._loaded:
                raise ConfigProblem("the file is missing")
            return config_mod.load(path)

        return self._parsed(
            read,
            f"it has no users, but sign-in was on; to turn sign-in off, also delete {self.signin_required}",
        )

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
                self.services.restore_from(datetime.now(UTC))
                self.versions.compact(max(config.retention_days, 365))
                self.monitor.restore_software(self.versions.latest(), config)
            except Exception:
                log.exception("cannot restore the state from the history; starting fresh")
        self.monitor.apply(config)

    async def start(self) -> None:
        if self.demo:
            log.info("demo mode: a made-up network, simulated results; config.yaml gives only its users")
            await asyncio.to_thread(self.demo.backfill, self.history, demo.config(), self.versions)
        self.reload()
        self.seed_from_env()
        self._tasks = [
            asyncio.create_task(self._poll_drivers(), name="poll drivers"),
            asyncio.create_task(self._watch_config(), name="watch config"),
            asyncio.create_task(self._expire_tickets(), name="expire tickets"),
            asyncio.create_task(self._relock(), name="key rings close as their policy says"),
            asyncio.create_task(self._watch_alarms(), name="alarms"),
            asyncio.create_task(self._watch_vulns(), name="vulnerabilities"),
            asyncio.create_task(self._renew_crl(), name="renew the OpenVPN CRL"),
            asyncio.create_task(self._prune(), name="prune history"),
            asyncio.create_task(self._resolve_hosts(), name="resolve device names"),
            asyncio.create_task(self._poll_hosts(), name="collect from hosts"),
            asyncio.create_task(self._heartbeat(), name="heartbeat"),
            asyncio.create_task(self._check_releases(), name="Kastellan releases"),
            asyncio.create_task(self._poll_routers(), name="OpenWrt devices"),
            asyncio.create_task(logs_poll.run(self), name="logs"),
            asyncio.create_task(self._poll_fritzboxes(), name="FRITZ!Boxes"),
            asyncio.create_task(devbackups.run(self), name="device backups"),
            asyncio.create_task(hubbackup.run(self), name="Kastellan's own backup"),
            asyncio.create_task(internet_api.run(self), name="internet connection"),
            asyncio.create_task(wifi_api.run(self), name="scheduled Wi-Fi scans"),
            asyncio.create_task(signins_api.run(self), name="sign-ins"),
            asyncio.create_task(self._check_vpn_helpers(), name="VPN helper versions"),
            asyncio.create_task(web_setup.run(self), name="names and certificates"),
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
        self.drop_clients()
        self.logs.close()
        self.vitals.close()
        if self._demo_dir:
            shutil.rmtree(self._demo_dir, ignore_errors=True)

    async def _resolve_hosts(self) -> None:
        """Device names to addresses (for which network each is in), and the hub's own address,
        every few minutes; soon after a config change too."""
        seen: frozenset[str] = frozenset()
        last = 0.0
        while True:
            hosts = frozenset(d.host for d in self.config.devices.values() if d.host)
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
            await asyncio.sleep(NAMES_LOOK)

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
                if device.act is not None and not self.demo:
                    await self.poll_actor(id)  # never raises
                try:
                    # With a collector, it lists the containers (except in the demo, which has none).
                    if device.docker is not None and (device.collect is None or self.demo):
                        await self._poll_docker(id, device.docker.wud)
                    if device.driver is not None:
                        await self._poll_driver(id, device)
                except Exception:
                    log.exception("asking %s failed", id)

        while True:
            await asyncio.gather(*(one(id, d) for id, d in list(self.config.devices.items())))
            await asyncio.sleep(DRIVER_EVERY)

    async def collect_host(self, id: str, device: Device, recheck: float = 0) -> None:
        """Read one host's collector now (host health; its containers, with updates from the
        registries). Also right after the actor changed something there, so pages see it. With
        `recheck`, the registries are asked anew too, waiting up to that many seconds for them."""
        status = self.monitor.status.get(id)
        if status is None or device.collect is None or self.demo:
            return
        try:
            report = await collector.collect(self.keys, device)
        except Exception as e:  # what it said last stays, marked by its time
            status.host_error = _brief(e)
            log.info("%s: the collector: %s", id, status.host_error)
            return
        now = datetime.now(UTC)
        status.host, status.host_checked, status.host_error = report, now, None
        self.disks.note(id, report, now)
        status.host_forecast = self.disks.forecasts(id, now)
        await self.note_vitals(id, status, from_host(id, report, self._cpu))
        samples = disks_of(id, report, self._busy, time.time())
        try:
            await asyncio.to_thread(self.vitals.add_disks, id, samples)
        except Exception:
            log.exception("%s: saving disk history failed", id)
        items = report.get("docker")
        if isinstance(items, list):
            try:
                self._set_containers(id, status, await docker.from_collector(items, self.registries, recheck))
                status.containers_error = None
            except Exception as e:
                status.containers_error = _brief(e)
                log.exception("%s: reading its containers failed", id)

    async def _demo_vitals(self) -> None:
        """The made-up devices' vitals now, and a week of them the first time (for the charts)."""
        ids = [i for i in (*demo.HOST, *demo.ROUTER, *demo_more.FRITZ) if i in self.monitor.status]
        now = time.time()
        if not self._demo_filled:
            self._demo_filled = True

            def fill() -> None:
                for i in ids:
                    for k in range(7 * 24 * 4, 0, -1):  # every 15 minutes
                        self.vitals.add(i, demo.vitals(i, now - k * 900), now=now - k * 900)
                        self.vitals.add_disks(i, demo.disks(i, now - k * 900), now=now - k * 900)

            await asyncio.to_thread(fill)
        for i in ids:
            await self.note_vitals(i, self.monitor.status[i], demo.vitals(i, now))
            await asyncio.to_thread(self.vitals.add_disks, i, demo.disks(i, now))

    async def note_vitals(self, id: str, status: DeviceStatus, v: Vitals) -> None:
        """A device's vitals now (shown) and, now and then, kept (charted)."""
        if v.empty():
            status.vitals = None
            return
        status.vitals = {**v.view(), "checked": datetime.now(UTC).isoformat(timespec="seconds")}
        try:
            await asyncio.to_thread(self.vitals.add, id, v)
        except Exception:
            log.exception("%s: keeping its vitals failed", id)

    async def _poll_hosts(self) -> None:
        """The collector on every device with `collect:`, every COLLECT_EVERY seconds."""
        limit = asyncio.Semaphore(4)

        async def one(id: str, device: Device) -> None:
            async with limit:
                await self.collect_host(id, device)

        while True:
            if self.demo:  # the made-up network's made-up reports
                for id, report in demo.HOST.items():
                    if status := self.monitor.status.get(id):
                        status.host, status.host_checked = report, datetime.now(UTC)
                        status.host_forecast = demo.FORECASTS.get(id)
                await self._demo_vitals()
            else:
                hosts = [(id, d) for id, d in list(self.config.devices.items()) if d.collect]
                self.disks.forget({id for id, _ in hosts})
                # Each host on its own: one host's trouble never stops the others, or the loop.
                await asyncio.gather(*(one(id, d) for id, d in hosts), return_exceptions=True)
            await asyncio.sleep(COLLECT_EVERY)

    # --- The key ring (vault.py) ----------------------------------------------------------------

    def admins(self) -> set[str]:
        return {name for name, u in self.config.users.items() if u.role is Role.ADMIN}

    async def keep_slot(self, user: str, password: str) -> None:
        """In every open ring that opens by password: this admin's slot opens with the password
        they just gave (made, or made again after a change); people no longer admins lose theirs."""
        account = self.config.users.get(user)
        if account is None or not self.vault.exists:
            return
        tag = password_tag(account.password)
        try:
            await asyncio.to_thread(self.vault.keep_slot, user, password, tag, self.admins())
        except Damaged as e:
            log.error("key ring: %s", e)

    async def keep_session(self, user: str, token: str | None) -> None:
        """This admin just gave their password in this session: their browser may open the
        rings whose policy has it, for SESSION_DAYS (vault.py)."""
        if not token or not self.vault.exists or user not in self.admins():
            return
        try:
            await asyncio.to_thread(
                self.vault.keep_session, user, Sessions.id_of(token), token, self.admins()
            )
        except Damaged as e:
            log.error("key ring: %s", e)

    def drop_session_slots(self) -> None:
        """Browser slots of sessions that ended (signed out, expired, removed) go."""
        with contextlib.suppress(Damaged):
            self.vault.drop_sessions(self.sessions.ids())

    async def unlock_on_signin(self, user: str, password: str, token: str | None = None) -> list[str]:
        """An admin signed in: open the rings their password opens, and keep their slots
        current (also this session's, `token`). Which rings this opened. Observers never open
        any; a damaged vault.json never stops a sign-in."""
        account = self.config.users.get(user)
        if account is None or account.role is not Role.ADMIN or not self.vault.exists:
            return []
        try:
            opened = await asyncio.to_thread(self.vault.unlock, user, password)
        except WrongKey:  # no slot yet, or one made with a password they no longer have
            opened = []
        except Damaged as e:
            log.error("key ring: %s", e)
            return []
        await self.keep_slot(user, password)
        await self.keep_session(user, token)
        with contextlib.suppress(Damaged):
            self.vault.note_stale(user, password_tag(account.password))
        self.sync_slots()
        return opened

    def open_rings_by_themselves(self) -> None:
        """Rings whose policy names a key file, an environment variable or a key in config.yaml
        (not those locked by hand since); then every open ring's slots follow its policy."""
        keys = {rid: r.key for rid, r in self.config.vault.rings.items() if r.key}
        try:
            opened = self.vault.unlock_by_itself(dict(os.environ), keys)
        except Damaged as e:
            log.error("key ring: %s", e)
            return
        for rid in opened:
            log.info("key ring %s: opened by itself", rid)
            self.audit.record("vault.unlock", None, None, ring=rid, by="its key")
        for rid, problem in self.vault.problems.items():
            log.warning("key ring %s: %s", rid, problem)
        self.sync_slots()

    def sync_slots(self) -> list[str]:
        """Each open ring's ways to open by itself match its policy now (keys read from where
        the policy says): a way no longer chosen loses its slot, so an old key (also one in a
        config backup) opens nothing any more. Notes on what couldn't be set up."""
        notes: list[str] = []
        keys = {rid: r.key for rid, r in self.config.vault.rings.items() if r.key}
        try:
            rings = self.vault.ring_ids()
        except Damaged:
            return notes
        for rid in rings:
            auto = [w for w in self.vault.policy(rid).unlock if w[0] in AUTO]
            if not self.vault.is_open(rid):
                if auto:
                    notes.append(f"{rid}: locked; its ways to open by itself are set once it is open")
                continue
            found: dict[tuple[str, str], str] = {}
            unread: set[tuple[str, str]] = set()
            for way, source in auto:
                secret, note = Vault._source(rid, way, source, dict(os.environ), keys)
                if note:
                    notes.append(f"{rid}: {note}")
                if secret is not None:
                    found[(way, source)] = secret
                else:  # chosen, but its key can't be read now: its slot stays
                    unread.add((way, source))
            try:
                if self.vault.set_ways(rid, found, frozenset(unread)):
                    self.audit.record("vault.ways", None, None, ring=rid, ways=[w for w, _ in found])
            except ValueError as e:  # it would be left with only its recovery key
                notes.append(f"{rid}: {e}; kept as it was")
        return notes

    def lock_vault(self, ring: str | None = None, by_hand: bool = True) -> list[str]:
        """Lock one ring (or all), and forget what was opened with Kastellan's (logins kept in
        memory). Which rings were open."""
        gone = self.vault.lock(ring, by_hand=by_hand)
        self._forget_if_locked()
        return gone

    def _forget_if_locked(self) -> None:
        """With the kastellan ring locked, what was opened with its secrets goes: push's key and
        the device clients (their logins)."""
        if not self.vault.is_open(KASTELLAN):
            self.push.forget_key()
            self.drop_clients()

    def drop_clients(self, id: str | None = None, ubus: bool = True, fritz: bool = True) -> None:
        """Forget the device clients (one device's, or all) and close their kept connections."""
        if fritz:
            for backoff in (self._wg_next, self._eco_next):  # a new login: try again now
                if id is None:
                    backoff.clear()
                else:
                    backoff.pop(id, None)
        for cache, wanted in ((self._ubus, ubus), (self._fritz, fritz)):
            if not wanted:
                continue
            for k in [k for k in cache if id is None or k[0] == id]:
                cache.pop(k).pool.close()
        if fritz:
            for k in [k for k in self._fritz_web if id is None or k == id]:
                del self._fritz_web[k]

    async def _relock(self) -> None:
        """Rings that stay open only while an admin uses the app (within ACTIVE_WINDOW), or until
        idle, close then."""
        while True:
            await asyncio.sleep(RELOCK_EVERY)
            using = bool(self.sessions.active(ACTIVE_WINDOW) & self.admins())
            self.drop_session_slots()
            for rid in self.vault.relock(using):
                log.info("key ring %s: closed (its policy)", rid)
                self.audit.record("vault.lock", None, None, ring=rid, by="its policy")
            self._forget_if_locked()

    def hub_backup_key(self) -> bytes:
        """The key of Kastellan's own backup (made from its passphrase; hubbackup.py)."""
        return base64.b64decode(json.loads(self.secrets.get(HUB_BACKUP_KEY))["key"])

    def own_secret(self, name: str, make=None) -> str:
        """A secret Kastellan makes for itself (a key, a password only it uses): read, or made
        the first time (`make()`, default a random token) and kept."""
        try:
            return self.secrets.get(name)
        except KeyError:
            value = make() if make else token_urlsafe(24)
            self.secrets.set(name, value)
            return value

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
        password = self.secrets.get(self.openwrt_secret(id))
        if client is None or client.password != password:
            client = Ubus(id, device.host, device.openwrt.port, device.openwrt.user, password, self.pins)
            self.drop_clients(id, fritz=False)
            self._ubus[key] = client
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
                    with contextlib.suppress(Exception):
                        access = await ubus.slow("session", "access", {})
                        groups = access.get("access-group") or {}
                        status.router_abilities = sorted(
                            g.removeprefix("kastellan-") for g in groups if g.startswith("kastellan-")
                        )
                    if status.router_abilities is not None:
                        try:
                            versions = await ubus.slow("file", "read", {"path": openwrt_setup.VERSIONS})
                            text = str(versions.get("data") or "")
                        except Exception:  # set up before versions were kept (or not readable)
                            text = None
                        status.router_outdated = openwrt_setup.outdated(text)
                    old = status.router or {}
                    openwrt_client.note_off_air(report, old, time.time())
                    report["packages"] = old.get("packages")
                    report["packages_checked"] = old.get("packages_checked")
                    if err := (old.get("errors") or {}).get("packages"):
                        report["errors"]["packages"] = err
                    report["packages_skipped"] = openwrt_client.packages_skipped(report)
                    if report["packages_skipped"]:
                        report["packages"] = None
                        report["errors"].pop("packages", None)
                    now = time.time()
                    due = not report["packages_checked"] or now - report["packages_checked"] > PACKAGES_EVERY
                    if due and not report["packages_skipped"]:
                        try:
                            report["packages"] = await openwrt_client.upgradable(ubus)
                            report["packages_checked"] = now
                            report["errors"].pop("packages", None)
                        except Exception as e:  # asked again only after the same while
                            report["packages"] = None
                            report["errors"]["packages"] = _brief(e)
                            report["packages_checked"] = now
                    versions = await self.releases.get()
                    release = (report["board"].get("release") or {}).get("version") or ""
                    advice = advise(release, versions) if versions else None
                    report["firmware"] = advice.view() if advice else None
                except Exception as e:  # what it said last stays, marked by its time
                    status.router_error = _brief(e)
                    log.info("%s: %s", id, status.router_error)
                    for vid in self.openvpns_on(id):  # nobody is known to be connected now
                        was = self.ovpn_status.get(vid, {})
                        self.ovpn_status[vid] = {**was, "connected": [], "error": status.router_error}
                    return
                status.router, status.router_checked, status.router_error = report, datetime.now(UTC), None
                self.services.note(id, report, status.router_checked)
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
                helper = None
                with contextlib.suppress(Exception):
                    helper = await openwrt_client.vitals(ubus)
                await self.note_vitals(id, status, from_openwrt(id, report, helper, self._cpu))
                vids = self.openvpns_on(id)
                if vids or "openvpn" in (status.router_abilities or []):
                    await self._read_openvpn(vids, id, ubus)
                if "bandwidth" in (status.router_abilities or []):
                    await self._read_bandwidth(id, ubus)
                if "roaming" in (status.router_abilities or []):
                    await self._read_roaming(id, ubus)

        while True:
            if self.demo:  # the made-up network's made-up access points
                now = datetime.now(UTC)
                for id, report in demo.ROUTER.items():
                    if status := self.monitor.status.get(id):
                        if status.router is None:  # the first look: a day of the services' history
                            for k in range(288, 0, -1):
                                self.services.note(id, report, now - timedelta(minutes=5 * k))
                        status.router, status.router_checked = report, now
                        self.services.note(id, report, now)
                checked = datetime.now(UTC).isoformat(timespec="seconds")
                self.vpn_servers = {id: {**v, "checked": checked} for id, v in demo.VPN_SERVERS.items()}
                self.bandwidth = {id: {**v, "checked": checked} for id, v in demo.BANDWIDTH.items()}
                self.roaming = {id: {**v, "checked": checked} for id, v in demo.ROAMING.items()}
            else:
                routers = [(id, d) for id, d in list(self.config.devices.items()) if d.openwrt]
                self.services.forget({id for id, _ in routers})
                await asyncio.gather(*(one(id, d) for id, d in routers), return_exceptions=True)
            await asyncio.sleep(ROUTER_EVERY)

    # --- FRITZ!Boxes (fritzbox/) ---------------------------------------------------------------

    def fritz_secret(self, id: str) -> str:
        device = self.config.devices[id]
        return (device.fritzbox.password_secret if device.fritzbox else None) or f"fritzbox-{id}"

    def fritz(self, id: str) -> Fritz:
        """The FRITZ!Box's TR-064 with Kastellan's user (kept: its service list is read once)."""
        device = self.config.devices[id]
        assert device.fritzbox is not None
        key = (id, device.host, device.fritzbox.port, device.fritzbox.user)
        client = self._fritz.get(key)
        password = self.secrets.get(self.fritz_secret(id))
        if client is None or client.password != password:
            client = Fritz(
                id, device.host, device.fritzbox.port, device.fritzbox.user, password, self.fritz_pins
            )
            self.drop_clients(id, ubus=False)
            self._fritz[key] = client
        return client

    def fritz_web(self, id: str):
        """The FRITZ!Box's web interface API (fritzbox/web.py), with its TR-064 client's user."""
        box = self.fritz(id)
        web = self._fritz_web.get(id)
        if web is None or web.fb is not box:  # a new client (login, address): its own session
            web = self._fritz_web[id] = fritz_web_api.Web(box)
        return web

    async def _poll_fritzboxes(self) -> None:
        """Every FRITZ!Box with a login: its state every FRITZ_EVERY seconds."""

        async def one(id: str) -> None:
            status = self.monitor.status.get(id)
            if status is None:
                return
            try:
                async with asyncio.timeout(FRITZ_TIMEOUT):
                    report = await fritz_client.read(self.fritz(id))
                    # The web interface's own API (undocumented): WireGuard. Every WG_EVERY, the
                    # last answer kept between; after a failure (no such page, no right: a
                    # sign-in each time) later and later, up to WG_BACKOFF_MAX.
                    due, failures = self._wg_next.get(id, (0.0, 0))
                    before = status.fritz or {}
                    if time.monotonic() < due:
                        if "wireguard" in before:
                            report["wireguard"] = before["wireguard"]
                        if err := (before.get("errors") or {}).get("wireguard"):
                            report["errors"]["wireguard"] = err
                    else:
                        try:
                            async with asyncio.timeout(WEB_TIMEOUT):
                                report["wireguard"] = fritz_web_api.wireguard(
                                    await self.fritz_web(id).data("shareWireguard")
                                )
                            self._wg_next[id] = (time.monotonic() + WG_EVERY, 0)
                        except Exception as e:
                            report["errors"]["wireguard"] = _brief(e)
                            wait = min(WG_BACKOFF_MAX, WG_BACKOFF * 2**failures)
                            self._wg_next[id] = (time.monotonic() + wait, failures + 1)
            except Exception as e:
                status.fritz_error = _brief(e)
                log.info("%s: %s", id, status.fritz_error)
                return
            old = (status.fritz or {}).get("internet", {}).get("rate") or {}
            new = report["internet"].get("rate") or {}
            if (
                all(isinstance(r.get(k), (int, float)) for r in (new, old) for k in ("rx", "tx", "at"))
                and new["at"] > old["at"]
                and new["rx"] >= old["rx"]
                and new["tx"] >= old["tx"]
            ):  # A reset or wrap is unknown, not a huge burst of traffic.
                span = new["at"] - old["at"]
                new["down"] = (new["rx"] - old["rx"]) * 8 / span
                new["up"] = (new["tx"] - old["tx"]) * 8 / span
            status.fritz, status.fritz_checked, status.fritz_error = report, datetime.now(UTC), None
            firmware = firmware_name(report.get("firmware"))  # spelled as the driver spells it
            status.info = {"model": report.get("model"), "name": None, "firmware": firmware}
            status.info_checked = datetime.now(UTC)
            if firmware:
                self._note_firmware(id, firmware, datetime.now(UTC))
            await self._fritz_vitals(id, status)

        while True:
            if self.demo:
                for id, report in demo_more.FRITZ.items():
                    if status := self.monitor.status.get(id):
                        status.fritz, status.fritz_checked = report, datetime.now(UTC)
            else:
                boxes = [id for id, d in list(self.config.devices.items()) if d.fritzbox]
                await asyncio.gather(*(one(id) for id in boxes), return_exceptions=True)
            await asyncio.sleep(FRITZ_EVERY)

    async def _fritz_vitals(self, id: str, status: DeviceStatus) -> None:
        """A FRITZ!Box's CPU, RAM and temperature from its energy monitor (the web interface's
        `ecoStat`, undocumented): every ECO_EVERY, later and later after failures (like
        WireGuard: each try may be a sign-in)."""
        due, failures = self._eco_next.get(id, (0.0, 0))
        if time.monotonic() < due:
            return
        try:
            async with asyncio.timeout(WEB_TIMEOUT):
                v = from_fritz(await self.fritz_web(id).data("ecoStat"))
            if v.empty():
                raise ValueError("the energy monitor said nothing readable")
            self._eco_next[id] = (time.monotonic() + ECO_EVERY, 0)
        except Exception as e:
            log.info("%s: its energy monitor: %s", id, _brief(e))
            wait = min(WG_BACKOFF_MAX, WG_BACKOFF * 2**failures)
            self._eco_next[id] = (time.monotonic() + wait, failures + 1)
            return
        await self.note_vitals(id, status, v)

    def openvpns_on(self, device: str) -> list[str]:
        """The OpenVPN VPNs whose server is this device."""
        return [v for v, s in self.config.vpns.items() if s.kind == "openvpn" and s.server == device]

    def seen(self, vid: str) -> Seen:
        """When each client of an OpenVPN VPN was last seen connected."""
        if vid not in self._seen:
            self._seen[vid] = Seen(self.settings.data / "vpn", vid)
        return self._seen[vid]

    async def _read_bandwidth(self, id: str, ubus: Ubus) -> None:
        """Bytes per device on this router (nlbwmon) and the rates since the last read."""
        now = time.time()
        was = self.bandwidth.get(id) or {}
        try:
            counts = await openwrt_client.bandwidth(ubus)
        except Exception as e:
            self.bandwidth[id] = {**was, "error": _brief(e)}
            return
        before, then = was.get("clients") or {}, was.get("t")
        clients = {}
        for mac, c in counts.items():
            entry: dict[str, Any] = {"rx": c["rx"], "tx": c["tx"], "rx_rate": None, "tx_rate": None}
            old = before.get(mac)
            if old and then and now > then:
                for k in ("rx", "tx"):
                    delta = c[k] - old[k]
                    entry[f"{k}_rate"] = round(delta * 8 / (now - then)) if delta >= 0 else None
            clients[mac] = entry
        self.bandwidth[id] = {
            "t": now,
            "checked": datetime.now(UTC).isoformat(timespec="seconds"),
            "clients": clients,
            "error": None,
        }

    async def _read_roaming(self, id: str, ubus: Ubus) -> None:
        """What usteer on this access point knows: its own clients, the other nodes (roaming)."""
        try:
            self.roaming[id] = {
                **await openwrt_client.roaming(ubus),
                "checked": datetime.now(UTC).isoformat(timespec="seconds"),
                "error": None,
            }
        except Exception as e:
            self.roaming[id] = {**(self.roaming.get(id) or {}), "error": _brief(e)}

    async def _read_openvpn(self, vids: list[str], id: str, ubus: Ubus) -> None:
        """Who is connected to the OpenVPN servers on this router now (its OpenWrt login's
        `openvpn` ability), read once and split by server: each VPN gets only its own server's
        clients (its `instance:`, or the one server there is), so last seen stays per VPN."""
        now = datetime.now(UTC)
        try:
            text = await openwrt_client.openvpn_status(ubus)
        except Exception as e:  # not granted (set up its login again), or it doesn't answer
            self.vpn_servers[id] = {"checked": now.isoformat(timespec="seconds"), "error": _brief(e)}
            for vid in vids:
                was = self.ovpn_status.get(vid, {})
                self.ovpn_status[vid] = {**was, "connected": [], "error": _brief(e)}
            return
        servers, none = split_status(text)
        self.vpn_servers[id] = {
            "checked": now.isoformat(timespec="seconds"),
            "error": None,
            "servers": {
                name: {**server_meta(part), "connected": [asdict(c) for c in parse_status(part)]}
                for name, part in servers.items()
            },
        }
        for vid in vids:
            s = self.config.vpns.get(vid)
            if not isinstance(s, OpenVpnSource):
                continue  # removed from config.yaml meanwhile
            instance = s.instance
            connected: list = []
            error = None
            if none or not servers:
                error = f"no OpenVPN server found on {id}"
            elif instance is None and len(servers) > 1:
                there = ", ".join(sorted(servers))
                error = f"several OpenVPN servers on {id} ({there}): choose its instance in Settings"
            elif instance is not None and instance not in servers:
                error = f"no OpenVPN server {instance!r} on {id} (there: {', '.join(sorted(servers))})"
            else:
                part = servers[instance or next(iter(servers))]
                connected = parse_status(part)
                meta = server_meta(part)
                if meta["running"] is False or (meta["age"] is not None and meta["age"] > OVPN_STALE):
                    connected = []
                    error = f"the OpenVPN server on {id} is stopped or its status is stale"
                markers = {ln for ln in part.splitlines() if ln.startswith("!")}
                if not connected and "!no-status" in markers:
                    error = f"the OpenVPN server on {id} keeps no status file (`status` in its config)"
                elif not connected and "!unreadable" in markers:
                    error = f"the OpenVPN status file on {id} can't be read"
                self.seen(vid).note(connected, now)  # only what was read: no guessing
            self.ovpn_status[vid] = {
                "checked": now.isoformat(timespec="seconds"),
                "connected": [asdict(c) for c in connected],
                "error": error,
            }

    async def _check_releases(self) -> None:
        """Whether a newer Kastellan is out, once a day (releases.py); the Updates page shows it
        and an info event tells it once."""
        await asyncio.sleep(RELEASES_FIRST)
        while True:
            if self.config.check_releases and not self.demo:
                try:
                    async with httpx.AsyncClient() as client:
                        self.latest_release = await releases.latest(client)
                except Exception as e:
                    log.info("can't ask for Kastellan's releases: %s", _brief(e))
            await asyncio.sleep(releases.EVERY)

    def newer_release(self) -> releases.Release | None:
        """A newer Kastellan, if one is out (and asking for releases is on)."""
        return releases.newer(self.latest_release if self.config.check_releases else None, __version__)

    async def _heartbeat(self) -> None:
        """Call the heartbeat URL every minute while the hub runs (a dead man's switch)."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
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

    async def poll_actor(self, id: str) -> None:
        """Which of the actor's scripts on the host are older than the hub's (for Updates)."""
        status, device = self.monitor.status.get(id), self.config.devices.get(id)
        if status is None or device is None:
            return
        try:
            allowed, status.actor_outdated = await actor.ask(self.keys, device)
            status.actor_abilities = sorted(allowed)
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
            reason = _brief(e)
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
                    self.config,
                    self.monitor.status,
                    now,
                    self.alarms.muted_now(now),
                    self.started,
                    unjudged,
                    self.ups_switched,
                )
                problems += self._vuln_problems()
                problems += self.backups.problems(self.config, now)
                problems += devbackups.problems(self, now)
                problems += self._dhcp_problems()
                problems += hubbackup.problems(self, time.time())
                problems += internet_api.problems(self)
                problems += roaming.problems(self)
                problems += web_setup.problems(self)
                problems += self.alarms.carry(self._vuln_keys_unknown())
                problems += self.alarms.carry(unjudged)
                # Muted or removed devices: their alarms end without a "back up".
                quiet = set(self.alarms.muted_now(now))
                # (Kastellan's own alarms have no device: "" is the hub, never a removed device.)
                quiet |= {
                    a.device
                    for a in self.alarms.open.values()
                    if a.device and a.device not in self.config.devices
                }
                for device in self.alarms.ended_maintenance(now):
                    self.audit.record("device.maintenance.end", None, device=device, by="time")
                held = set(maintenance.active(self.config, now, self.alarms.maintenance))
                remind = timedelta(hours=self.config.alarms.remind_hours) or None
                problems = [p for p in problems if p.device not in quiet]
                messages = self.alarms.update(problems, now, quiet, held, remind)
                messages += self._events(now, send=not first)
                first = False
                await self._send(messages, now)
                await self._retry_outbox(now)
            except Exception:
                log.exception("evaluating the alarms failed")

    async def _send(self, messages: list[Message], now: datetime) -> None:
        """Send, and keep what failed for _retry_outbox."""
        quiet = self._quiet_notifications(now)
        messages = [m for m in messages if m.device not in quiet]
        failed = await notify.send(
            self.config.notify, messages, now, push=self.push, secrets=self.secrets.get
        )
        self._outbox.extend((c, m, now, now, 1) for c, m in failed)

    def _quiet_notifications(self, now: datetime) -> set[str]:
        return set(self.alarms.muted_now(now)) | set(
            maintenance.active(self.config, now, self.alarms.maintenance)
        )

    async def _retry_outbox(self, now: datetime) -> None:
        """Retry failed notifications: after 1, 2, 4 ... 32 minutes from the last try, for a day;
        all due ones at once, within one time limit (a dead ntfy must not hold up the alarms)."""
        due, later = [], []
        quiet = self._quiet_notifications(now)
        for channel, message, first_try, last_try, tries in self._outbox:
            if message.device in quiet:
                continue  # maintenance ends with a fresh alarm if the problem remains
            if (now - first_try).total_seconds() > OUTBOX_KEEP:
                log.warning("giving up on a notification after a day: %s", message.title)
            elif channel not in self.config.notify:
                continue  # the channel was removed from config.yaml
            elif (now - last_try).total_seconds() >= ALARMS_EVERY * 2 ** min(tries, 6):
                due.append((channel, message, first_try, tries))
            else:
                later.append((channel, message, first_try, last_try, tries))
        self._outbox = later
        # Push tries each browser again on its own (push.py), within the same kind of limit.
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(SEND_TIMEOUT):
                    await self.push.retry_due(client, blocked_urls={f"/devices/{d}" for d in quiet})
        if not due:
            return

        async def one(channel: NotifyChannel, message: Message) -> bool:
            return not await notify.send([channel], [message], now, push=self.push, secrets=self.secrets.get)

        try:
            async with asyncio.timeout(SEND_TIMEOUT):
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
        # A sign-in from an outside address that never succeeded before (signins_api.run).
        for src in self.signin_flags:
            who = ", ".join(src.get("users") or []) or "someone"
            where = ", ".join(
                self.config.label(d) for d in src.get("devices") or [] if d in self.config.devices
            )
            title = f"Sign-in from a new outside address: {src['address']}"
            body = f"{who} · {', '.join(src.get('services') or [])}" + (f" on {where}" if where else "")
            if m := self.alarms.event(f"signin-new:{src['address']}", title, body, None, now, send):
                out.append(m)
        for id, status in self.monitor.status.items():
            device = self.config.devices.get(id)
            if device is None:
                continue
            label = self.config.label(id)
            for c in status.containers or []:
                if c.get("update") and not self.skipped.is_skipped(id, c):
                    target = c.get("update_to") or "a newer image"
                    key = f"update:{id}:{c.get('key', c['name'])}:{target}"
                    title = f"{label}: {c['name']} can be updated to {target}"
                    if m := self.alarms.event(key, title, f"{c['image']}:{c['tag']}", id, now, send):
                        out.append(m)
            # An OpenWrt device's newer point release (within its series): once per release.
            fw = (status.router or {}).get("firmware") or {}
            if fw.get("newest_in_series"):
                key = f"openwrt:{id}:{fw['newest_in_series']}"
                title = f"{label}: OpenWrt {fw['newest_in_series']} is out (it runs {fw.get('current')})"
                if m := self.alarms.event(key, title, "a bug-fix release of its series", id, now, send):
                    out.append(m)
            # A FRITZ!Box: an update FRITZ!OS offers; a new port forward or way in from outside.
            fritz = status.fritz or {}
            if (fritz.get("update") or {}).get("available"):
                version = fritz["update"].get("version") or "a newer FRITZ!OS"
                if m := self.alarms.event(
                    f"fritzos:{id}:{version}", f"{label}: {version} can be installed", "", id, now, send
                ):
                    out.append(m)
            for f in fritz.get("forwards") or []:
                if f.get("enabled"):
                    what = f"{f.get('protocol')} {f.get('port')} → {f.get('to')}:{f.get('to_port')}"
                    if m := self.alarms.event(
                        f"forward:{id}:{what}",
                        f"{label}: a port forward is on: {what}",
                        f.get("name") or "",
                        id,
                        now,
                        send,
                    ):
                        out.append(m)
            remote = fritz.get("remote") or {}
            for way, name in (("remote_access", "remote access (HTTPS)"), ("myfritz", "MyFRITZ!")):
                key, title = f"fritz-{way}:{id}", f"{label}: {name} is on"
                if remote.get(way) and (m := self.alarms.event(key, title, "a way in", id, now, send)):
                    out.append(m)
            # Devices that got an address for the first time (a router's DHCP leases): said once;
            # a router seen with many at once (just set up) says how many, not each.
            pool = (status.router or {}).get("dhcp") or fritz.get("dhcp") or {}
            known_hosts = {d.host for d in self.config.devices.values() if d.host}
            fresh = []
            for lease in pool.get("leases") or []:
                mac, addr = lease.get("mac"), lease.get("address")
                if not mac or addr in known_hosts:
                    continue
                seen = f"newdevice:{mac}" in self.alarms.seen_events
                self.alarms.event(f"newdevice:{mac}", "", "", id, now, send=False)
                if not seen:
                    fresh.append(f"{lease.get('name') or mac} ({addr})")
            if fresh and send:
                if len(fresh) <= 3:
                    for f in fresh:
                        out.append(Message(Severity.INFO, f"New on the network: {f}", f"from {label}", id))
                else:
                    out.append(
                        Message(
                            Severity.INFO,
                            f"{len(fresh)} devices new on the network",
                            "; ".join(fresh[:8]),
                            id,
                        )
                    )
            # The host wants a reboot (kernel or libc updates): once per set of packages.
            updates = (status.host or {}).get("updates") or {}  # normalized (host/report.py)
            if updates.get("reboot") and "reboot" not in self.skipped.device_skipped(id, None, status.host):
                why = ", ".join(str(p) for p in (updates.get("reboot_for") or [])[:6])
                key = f"reboot:{id}:{why}"
                if m := self.alarms.event(
                    key, f"{label} wants a reboot", why or "after updates", id, now, send
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
                        note = {
                            "upstream": " (maybe fixed by the distribution)",
                            "patch": " (patch level unknown: check the device)",
                        }.get(f.get("confidence"), "")
                        new.append(f"{f['id']} in {item['product']}{note}")
            if new:
                noun = "vulnerability" if len(new) == 1 else "vulnerabilities"
                title = f"{label}: {len(new)} new known {noun}"
                out.append(Message(Severity.INFO, title, "; ".join(new[:8]), id))
        if new_release := self.newer_release():
            key = f"kastellan:{new_release.version}"
            title = f"Kastellan {new_release.version} is out (this hub runs {__version__})"
            if m := self.alarms.event(key, title, new_release.url, None, now, send):
                out.append(m)
        # A minute of overlap: the log keeps whole seconds; seen_events keeps it from repeating.
        for change in self.versions.changes(self._events_since - timedelta(seconds=60)):
            device = self.config.devices.get(change["device"])
            name = self.config.label(change["device"]) if device else change["device"]
            key = f"version:{change['device']}:{change['check']}:{change['to']}"
            what = change["check"].removeprefix("@")
            title = f"{name}: {what} updated"
            if m := self.alarms.event(
                key, title, f"{change['from']} → {change['to']}", change["device"], now, send
            ):
                out.append(m)
        self._events_since = now
        return out

    def _dhcp_problems(self) -> list[Problem]:
        """DHCP: a router's pool nearly used up (warning, from 90 %); a DHCP server answering that
        isn't the one a device's dhcp check expects (warning: a second router, a misconfigured
        device, or someone handing out addresses)."""
        out: list[Problem] = []
        now = datetime.now(UTC)
        for id, status in self.monitor.status.items():
            if id not in self.config.devices:
                continue
            checked = status.router_checked if status.router else status.fritz_checked
            if (
                checked is None
                or checked < self.started
                or (now - checked).total_seconds() > 3 * ROUTER_EVERY
            ):
                continue
            pool = ((status.router or {}).get("dhcp") or (status.fritz or {}).get("dhcp")) or {}
            size, used = pool.get("size"), pool.get("used")
            if (
                pool.get("server") is not False
                and size
                and size > 0
                and used is not None
                and used >= 0.9 * size
            ):
                label = self.config.label(id)
                out.append(
                    Problem(
                        f"dhcp:pool:{id}",
                        Severity.WARNING,
                        id,
                        f"{label}: DHCP pool",
                        f"{label}: its DHCP pool is nearly used up ({used} of {size})",
                        "new devices may get no address",
                    )
                )
        hosts = {
            d.host: id
            for id, d in self.config.devices.items()
            if d.host and any(c.type == "dhcp" for c in d.checks)
        }
        expected = set(hosts) | {self.resolver.address(host) for host in hosts}
        for server, seen in dhcp.UNEXPECTED.items():
            id = hosts.get(seen["device"])
            if id is None or server in expected or time.time() - seen["seen"] > dhcp.UNEXPECTED_FOR:
                continue
            known = next(
                (
                    i
                    for i, d in self.config.devices.items()
                    if d.host and (d.host == server or self.resolver.address(d.host) == server)
                ),
                None,
            )
            who = self.config.label(known) if known else server
            out.append(
                Problem(
                    f"dhcp:rogue:{server}",
                    Severity.WARNING,
                    id,
                    f"DHCP server {server}",
                    f"Another DHCP server answers: {who}",
                    f"it offered {seen['offered']}; only {self.config.label(id)} is expected",
                )
            )
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
                    title = f"{self.config.label(id)}: {what}{f['id']} in {item['product']}"
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
                # By policy, only a new build of the tag it runs: going to another version tag
                # is a person's decision (Updates), never automatic.
                if c.get("update_to") != NEWER_BUILD:
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
            self.spawn(self._update_one(id, device, c, reason), name=f"update {id} {key}")

    def spawn(self, run: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
        """`run` as one of the hub's own tasks: kept until done, cancelled when the hub stops, and
        its error read (logged) instead of left for the garbage collector to complain about."""
        task = asyncio.create_task(run, name=name)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(_log_failure)
        return task

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
        title = f"{self.config.label(id)}: updating {c['name']} to {c.get('update_to')} ({outcome})"
        # Through the outbox, like every alarm: retried when a channel can't be reached.
        await self._send([Message(severity, title, f"because of {reason}", id)], datetime.now(UTC))

    def openvpn(self, vid: str) -> OpenVpn:
        """An OpenVPN VPN's client (its PKI host and server)."""
        s = self.config.vpns[vid]
        assert s.kind == "openvpn"
        devices = self.config.devices
        return OpenVpn(self.keys, s, devices[s.pki], devices[s.server] if s.server else None)

    async def _check_vpn_helpers(self) -> None:
        """Twice a day: whether the scripts Kastellan's VPN key runs are this hub's versions."""
        await asyncio.sleep(VPN_HELPERS_FIRST)
        while True:
            if not self.demo:
                for vid, s in list(self.config.vpns.items()):
                    if s.kind != "openvpn":
                        continue
                    try:
                        self.vpn_outdated[vid] = await self.openvpn(vid).outdated_helpers(
                            self.vpn_outdated.get(vid)
                        )
                    except Exception as e:  # its devices are gone from the config: next time
                        log.info("VPN helpers of %s: %s", vid, _brief(e))
                self.vpn_outdated = {v: o for v, o in self.vpn_outdated.items() if v in self.config.vpns}
            await asyncio.sleep(VPN_HELPERS_EVERY)

    def vpn_outdated_on(self, device: str) -> list[str]:
        """The VPN helper scripts on this device older than this hub's ("vpn-pki", "vpn-crl")."""
        out = []
        for vid, names in self.vpn_outdated.items():
            s = self.config.vpns.get(vid)
            if s is None:
                continue
            if "pki" in names and s.pki == device and "vpn-pki" not in out:
                out.append("vpn-pki")
            if "crl" in names and s.server == device and "vpn-crl" not in out:
                out.append("vpn-crl")
        return out

    async def push_crl(self, vid: str, renew: bool = False) -> None:
        """Bring a VPN's CRL to its OpenVPN server and note when it expires."""
        try:
            pem = await self.openvpn(vid).push_crl(renew)
        except Exception as e:
            self.crl_state[vid] = {**self.crl_state.get(vid, {}), "error": f"{type(e).__name__}: {e}"}
            raise
        self.crl_state[vid] = {
            "pushed": datetime.now(UTC).isoformat(timespec="seconds"),
            "next_update": crl_next_update(pem),
            "error": None,
        }

    async def _renew_crl(self) -> None:
        """Renew and push the CRL daily: easy-rsa's CRLs last 180 days, and OpenVPN refuses
        every client once its CRL has expired."""
        await asyncio.sleep(CRL_FIRST)
        while True:
            for vid, s in list(self.config.vpns.items()):
                # One VPN's trouble (or its removal meanwhile) never stops the others' renewal.
                if vid not in self.config.vpns or s.kind != "openvpn" or not s.server or self.demo:
                    continue
                try:
                    await self.push_crl(vid, renew=True)
                    self.audit.record("vpn.openvpn.crl", None, None, vpn=vid, outcome="renewed daily")
                except Exception as e:
                    log.warning("renewing the CRL of %s failed: %s", vid, e)
                    if vid not in self.config.vpns:
                        continue  # removed while it ran
                    title = f"{s.name or vid}: the CRL couldn't be renewed"
                    with contextlib.suppress(Exception):
                        await self._send([Message(Severity.WARNING, title, str(e)[:200])], datetime.now(UTC))
            await asyncio.sleep(CRL_EVERY)

    async def _expire_tickets(self) -> None:
        """A ticket holds a desktop password until used; unused ones go after their minute."""
        while True:
            await asyncio.sleep(TICKETS_EVERY)
            self.tickets.expire()

    async def _prune(self) -> None:
        while True:
            try:
                for history in (self.history, self.services.history):
                    for path in history.prune(self.config.retention_days):
                        log.info("history: deleted %s", path)
            except OSError as e:
                log.warning("cannot prune the history: %s", e)
            await asyncio.sleep(PRUNE_EVERY)
