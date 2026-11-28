"""Bringing log lines in (logs.py), every minute: fetched from the devices that allow it
(OpenWrt's logd, a FRITZ!Box's event log, Linux hosts' journals and containers' own logs through
the actor), made from wg-easy's handshakes (WireGuard presence), and taken from the TLS syslog
listener (verified, syslog_tls.py) and the open UDP syslog port when it is on (unverified). The
hub runs `run(hub)` as one task."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from . import logs
from .openwrt import client as ow

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

EVERY = 60
RECENT_LINES, ALL_LINES = 200, 1000  # an OpenWrt log read when caught up, and when not
DENIED_AGAIN = 1800  # a device that doesn't allow it is asked again after this long
PRUNE_EVERY = 3600


def _brief(e: BaseException) -> str:
    return (str(e) or type(e).__name__)[:200]


async def openwrt(hub: Hub, id: str) -> int:
    """New lines from an OpenWrt device's logd; how many."""
    key = f"openwrt:{id}"
    cursor = await asyncio.to_thread(hub.logs.get_state, key)
    if cursor and int(cursor.get("t", 0)) > (time.time() + logs.AHEAD) * 1000:
        # A garbage entry became the cursor (before entries were checked): go on from the
        # newest real line, so what logd still holds since then comes in.
        newest = await asyncio.to_thread(hub.logs.newest, id, ("syslog", "kernel"))
        cursor = {"t": round(newest * 1000) + 1, "ids": []} if newest else None
        log.warning("logs: %s's cursor was in the future; going on from %s", id, cursor)
        await asyncio.to_thread(hub.logs.set_state, key, cursor)
    # Caught up: the newest RECENT_LINES are enough (logd builds and the router encrypts all it
    # is asked for, every minute). All of them new: there may be more, so the whole buffer.
    entries = await ow.read_log(hub.ubus(id), lines=RECENT_LINES if cursor else ALL_LINES)
    lines, after = logs.from_logd(id, entries, cursor)
    # Compare against all parsable entries: a malformed line isn't evidence of overlap.
    if cursor and len(entries) >= RECENT_LINES and len(lines) == len(logs.from_logd(id, entries, None)[0]):
        entries = await ow.read_log(hub.ubus(id), lines=ALL_LINES)
        lines, after = logs.from_logd(id, entries, cursor)
    cursor = after
    if lines:
        await asyncio.to_thread(hub.logs.add, lines)
        await asyncio.to_thread(hub.logs.set_state, key, cursor)
    return len(lines)


async def fritzbox(hub: Hub, id: str) -> int:
    """New lines from a FRITZ!Box's event log."""
    from .fritzbox import client as fb

    text = await fb.device_log(hub.fritz(id))
    key = f"fritzbox:{id}"
    cursor = await asyncio.to_thread(hub.logs.get_state, key)
    lines, cursor = fb.log_lines(id, text, cursor)
    if lines:
        await asyncio.to_thread(hub.logs.add, lines)
        await asyncio.to_thread(hub.logs.set_state, key, cursor)
    return len(lines)


def _missing(e: Exception) -> bool:
    return isinstance(e, RuntimeError) and ("isn't installed" in str(e) or "unknown action" in str(e))


LEVEL_WORDS = (
    (3, re.compile(r"\b(FATAL|CRIT(ICAL)?|ERROR|ERR|PANIC)\b", re.I)),
    (4, re.compile(r"\bWARN(ING)?\b", re.I)),
)


def level_of(text: str, default: int = 6) -> int:
    """A container's line has no level: the words it starts with say one, roughly."""
    head = text[:80]
    for level, words in LEVEL_WORDS:
        if words.search(head):
            return level
    return default


async def host(hub: Hub, id: str) -> int:
    """A Linux host's journal and its containers' logs (the actor's `journal` and `docker-logs`
    abilities); NotAllowed when it has neither."""
    total, missing = 0, 0
    errors = []
    for read in (journal, docker_logs):
        try:
            total += await read(hub, id)
        except Exception as e:
            if _missing(e):
                missing += 1
            else:
                errors.append(f"{read.__name__}: {_brief(e)}")
    if errors:
        raise RuntimeError("; ".join(errors))
    if missing == 2:
        raise NotAllowed(
            "not allowed: tick “Read the host's journal” (and containers' logs) in its actor setup"
        )
    return total


async def docker_logs(hub: Hub, id: str) -> int:
    """Every container's new lines since the last time (the actor's `docker-logs`)."""
    from .host import actor

    key = f"docker:{id}"
    since = await asyncio.to_thread(hub.logs.get_state, key) or "-"
    result = await actor.act(hub.keys, hub.config.devices[id], "docker-logs", since)
    lines = [
        logs.Line(
            float(x.get("t") or 0),
            id,
            "container",
            str(x.get("container") or "")[:64],
            level_of(str(x.get("msg") or "")),
            str(x.get("msg") or ""),
        )
        for x in result.get("lines") or []
    ]
    if lines:
        await asyncio.to_thread(hub.logs.add, lines)
    new = result.get("since") or since
    if new != since:
        await asyncio.to_thread(hub.logs.set_state, key, new)
    return len(lines)


async def journal(hub: Hub, id: str) -> int:
    """New lines of a Linux host's journal, through its actor's `journal` ability."""
    from .host import actor

    key = f"journal:{id}"
    cursor = await asyncio.to_thread(hub.logs.get_state, key) or "-"
    total = 0
    for _ in range(5):  # at most 2000 entries a call: catch up a few calls at a time
        result = await actor.act(hub.keys, hub.config.devices[id], "journal", cursor)
        lines = [
            logs.Line(
                float(x.get("t") or 0),
                id,
                "container" if x.get("container") else "journal",
                str(x.get("container") or x.get("program") or "")[:64],
                int(x.get("level", 6)),
                str(x.get("msg") or ""),
            )
            for x in result.get("lines") or []
        ]
        if lines:
            await asyncio.to_thread(hub.logs.add, lines)
        total += len(lines)
        new = result.get("cursor") or cursor
        if new != cursor:
            await asyncio.to_thread(hub.logs.set_state, key, new)
            cursor = new
        if len(lines) < 2000:
            break
    return total


class NotAllowed(Exception):
    denied = True


def sources(hub: Hub) -> dict[str, str]:
    """Which devices' logs the hub fetches, and how."""
    out: dict[str, str] = {}
    for id, d in hub.config.devices.items():
        if d.openwrt:
            out[id] = "openwrt"
        elif d.fritzbox:
            out[id] = "fritzbox"
        elif d.act is not None:
            out[id] = "journal"
    return out


async def fetch_all(hub: Hub) -> None:
    now = time.time()
    limit = asyncio.Semaphore(4)

    async def one(id: str, how: str) -> None:
        state = hub.log_status.get(id) or {}
        if state.get("denied") and now - state.get("tried", 0) < DENIED_AGAIN:
            return
        async with limit:
            entry: dict[str, Any] = {"how": how, "tried": now}
            try:
                fetch = {"openwrt": openwrt, "fritzbox": fritzbox, "journal": host}[how]
                n = await fetch(hub, id)
                entry |= {"ok": datetime.now(UTC).isoformat(timespec="seconds"), "error": None, "new": n}
            except ow.UbusError as e:
                denied = e.code in (6, ow.DENIED)
                entry |= {
                    "ok": state.get("ok"),
                    "denied": denied,
                    "error": "not allowed: tick “Read its system log” in its setup" if denied else _brief(e),
                }
            except Exception as e:
                entry |= {"ok": state.get("ok"), "error": _brief(e), "denied": getattr(e, "denied", False)}
            hub.log_status[id] = entry

    wanted = sources(hub)
    for gone in set(hub.log_status) - set(wanted):
        hub.log_status.pop(gone, None)
    await asyncio.gather(*(one(i, h) for i, h in wanted.items()), return_exceptions=True)


QUIET_AFTER = 5 * 60  # no handshake for this long: the peer has gone quiet (it renews every 2 min)


async def wireguard(hub: Hub) -> None:
    """WireGuard logs nothing itself: lines made from wg-easy's latest handshakes, when a
    client comes (a handshake after being quiet) and goes quiet. Presence, not proof of a login."""
    from urllib.parse import urlsplit

    from .vpn.wireguard import WgEasy

    now = time.time()
    wanted = {vid: vpn for vid, vpn in hub.config.vpns.items() if vpn.kind == "wireguard"}
    for gone in set(hub.vpn_addresses) - set(wanted):
        del hub.vpn_addresses[gone]
    for vid, vpn in wanted.items():
        try:
            clients = await WgEasy(vpn.wg_easy, vpn.user, hub.secrets.get(vpn.password_secret)).clients()
        except Exception:
            continue
        host_name = urlsplit(vpn.wg_easy).hostname or ""
        device = next(
            (
                i
                for i, d in hub.config.devices.items()
                if d.host and d.host in (host_name, hub.resolver.address(host_name))
            ),
            vid,
        )
        key = f"wireguard:{vid}"
        was: dict[str, bool] = await asyncio.to_thread(hub.logs.get_state, key) or {}
        now_on: dict[str, bool] = {}
        addresses: dict[str, str] = {}
        lines = []
        for c in clients:
            for a in (c.address or "").split(","):
                if a.strip():
                    addresses[a.strip().split("/")[0]] = f"{c.name} ({vpn.name or vid})"
            try:
                last = (
                    datetime.fromisoformat(c.last_handshake.replace("Z", "+00:00")).timestamp()
                    if c.last_handshake
                    else None
                )
            except ValueError:  # one odd entry doesn't stop the others
                continue
            active = last is not None and 0 <= now - last < QUIET_AFTER
            now_on[c.id] = active
            if active and not was.get(c.id):
                lines.append(
                    logs.Line(
                        last,
                        device,
                        "vpn",
                        f"wireguard({vpn.name or vid})",
                        6,
                        f"{c.name} connected (handshake; {c.address or 'no address'})",
                    )
                )
            elif not active and was.get(c.id):
                lines.append(
                    logs.Line(
                        now,
                        device,
                        "vpn",
                        f"wireguard({vpn.name or vid})",
                        6,
                        f"{c.name} went quiet (no handshake for {QUIET_AFTER // 60} min)",
                    )
                )
        hub.vpn_addresses[vid] = addresses  # this VPN's clients now (only these)
        if lines:
            await asyncio.to_thread(hub.logs.add, lines)
        await asyncio.to_thread(hub.logs.set_state, key, now_on)


class Syslog:
    """The open syslog port, started and stopped as config.yaml says."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.protocol: logs.SyslogReceiver | None = None
        self.port: int | None = None
        self.error: str | None = None

    async def apply(self, hub: Hub) -> None:
        want = hub.config.logs.syslog
        port = want.port if want.enabled and not hub.demo else None
        if port == self.port and (self.transport is not None or port is None):
            return
        self.stop()
        if port is None:
            return
        loop = asyncio.get_running_loop()

        def who(address: str) -> str | None:
            for id, d in hub.config.devices.items():
                if d.host and (d.host == address or hub.resolver.address(d.host) == address):
                    return id
            return None

        try:
            self.transport, self.protocol = await loop.create_datagram_endpoint(
                lambda: logs.SyslogReceiver(who), local_addr=("0.0.0.0", port)
            )
            self.port, self.error = port, None
            log.info("syslog: listening on UDP %d", port)
        except OSError as e:
            self.port, self.error = port, f"can't listen on UDP {port}: {e.strerror or e}"
            log.warning("syslog: %s", self.error)

    def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
        self.transport = self.protocol = None
        self.port = None

    def view(self) -> dict[str, Any]:
        p = self.protocol
        return {
            "listening": self.transport is not None,
            "port": self.port,
            "error": self.error,
            "received": p.received if p else 0,
            "refused": p.refused if p else 0,
        }


class SyslogTls:
    """The TLS syslog listener (syslog_tls.py), started and stopped as config.yaml says."""

    def __init__(self) -> None:
        self.server: asyncio.Server | None = None
        self.listener: Any = None
        self.port: int | None = None
        self.error: str | None = None
        self.tried = 0.0

    async def apply(self, hub: Hub) -> None:
        from . import syslog_tls

        want = hub.config.logs.syslog_tls
        port = want.port if want.enabled and not hub.demo else None
        if port == self.port and (self.server is not None or port is None):
            return
        if port is not None and self.server is None and port == self.port and time.time() - self.tried < 60:
            return  # it failed a moment ago (a locked key ring, a taken port): again in a minute
        await self.stop()
        self.port, self.tried = port, time.time()
        if port is None:
            return
        try:
            names = [n for n in (hub.own_address, hub.own_v6, *hub.hosts()) if n]
            ctx = await asyncio.to_thread(hub.syslog_ca.server_context, names)
            self.listener = syslog_tls.Listener(hub.syslog_ca, lambda d: d in hub.config.devices)
            self.server = await asyncio.start_server(
                self.listener.handle, "0.0.0.0", port, ssl=ctx, ssl_handshake_timeout=15
            )
            self.error = None
            log.info("syslog over TLS: listening on TCP %d", port)
        except Exception as e:
            self.server, self.listener = None, None
            self.error = f"can't listen on TCP {port}: {_brief(e)}"
            log.warning("syslog over TLS: %s", self.error)

    async def stop(self) -> None:
        if self.listener is not None:
            self.listener.active = False
        if self.server is not None:
            self.server.close()
            self.server.close_clients()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.server.wait_closed(), 2)
        self.server = None

    def view(self) -> dict[str, Any]:
        lst = self.listener
        return {
            "listening": self.server is not None,
            "port": self.port,
            "error": self.error,
            "received": lst.received if lst else 0,
            "refused": lst.refused if lst else 0,
        }


async def run(hub: Hub) -> None:
    async def poll() -> None:
        try:
            if hub.demo:
                from . import demo_more

                t = time.time()
                await asyncio.to_thread(hub.logs.add, demo_more.log_lines(t - EVERY, t, int(t)))
            else:
                await fetch_all(hub)
                await wireguard(hub)
        except Exception:
            log.exception("fetching logs failed")

    fetching: asyncio.Task | None = None
    last_prune = 0.0
    last_fetch = time.monotonic() - EVERY + 20  # the first fetch a little after the start
    try:
        while True:
            now = time.monotonic()
            with contextlib.suppress(Exception):
                await hub.syslog.apply(hub)
            with contextlib.suppress(Exception):
                await hub.syslog_tls.apply(hub)
            if hub.syslog_tls.listener is not None:
                lines = hub.syslog_tls.listener.take()
                if lines:
                    try:
                        await asyncio.to_thread(hub.logs.add, lines)
                    except Exception:
                        log.exception("syslog over TLS: storing lines failed")
            if hub.syslog.protocol is not None:
                lines = hub.syslog.protocol.take()
                if lines:
                    try:
                        await asyncio.to_thread(hub.logs.add, lines)
                    except Exception:
                        log.exception("syslog: storing lines failed")
            if now - last_fetch >= EVERY and (fetching is None or fetching.done()):
                last_fetch = now
                fetching = asyncio.create_task(poll())
            if now - last_prune >= PRUNE_EVERY:
                last_prune = now
                s = hub.config.logs
                try:
                    await asyncio.to_thread(hub.logs.prune, s.days, s.max_lines)
                except Exception:
                    log.exception("pruning logs failed")
            await asyncio.sleep(2)
    finally:
        hub.syslog.stop()
        await hub.syslog_tls.stop()
        if fetching is not None:
            fetching.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await fetching
