"""The terminal in the browser: a WebSocket per session, bridged to an SSH shell on the device.

    ws /api/devices/{id}/terminal?cols=120&rows=32

Browser → hub: text frames with JSON, {"type": "input", "data": "..."} and
{"type": "resize", "cols": c, "rows": r}. Hub → browser: binary frames with the terminal's
output, and text frames with JSON for what isn't output ({"type": "unknown-host", ...},
{"type": "error", ...}, {"type": "exit", ...}).

Only signed-in admins, only to hosts whose key an admin trusted (ssh.py). With `ssh:` the hub
logs in with its key; without (or when that key isn't accepted) the page is asked for a login
({"type": "login-needed", ...}) and answers {"type": "login", "user", "password"} (used for this
connection only; optionally stored in the People ring) or {"type": "login", "entry": <id>} (a
stored login, read for the signed-in admin who asked: vault.get(by=)). The audit log gets who
opened which terminal, how it logged in and how long it stayed open; never a password, never
what was typed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .access import USER
from .auth import Brake, password_tag
from .auth_api import (
    Who,
    client_address,
    require_admin,
    require_signin,
    same_origin,
    session_token,
    session_who,
)
from .config import CheckType, Device
from .ssh import ChangedHost, Target, UnknownHost
from .vault import PEOPLE, Locked

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

IDLE_LIMIT = 30 * 60  # a terminal nobody typed in for this long closes
MAX_SECONDS = 8 * 3600  # however busy, a terminal ends after this long
MAX_PER_USER = 4
RECHECK_EVERY = 15  # seconds between checks that the session is still good
LOGIN_WAIT = 300  # seconds the page has to answer "login-needed"
_brake = Brake()  # typed SSH passwords, per address: the device's own guard is the other one


def ssh_port(device: Device) -> int | None:
    """Where the device answers SSH: its `ssh:` port, else a port check that is SSH (named ssh, or
    port 22); None when nothing says it has SSH."""
    if device.ssh:
        return device.ssh.port
    for c in device.checks:
        port = str(c.target)
        if c.type is CheckType.TCP and port.isdigit() and (c.name.lower() == "ssh" or port == "22"):
            return int(port)
    return None


class Trust(BaseModel):
    fingerprint: str
    # The port a setup logs in on, for a device without `ssh:` yet; else the ssh: port.
    port: int | None = Field(default=None, ge=1, le=65535)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    def target(id: str, port: int | None = None) -> Target:
        """Where the device's SSH is; the user only matters for logging in (a host key belongs
        to the address and port)."""
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        port = port or ssh_port(device) or 22
        return Target(device.host, port, device.ssh.user if device.ssh else "root")

    @api.get("/ssh/key")
    def hub_key(request: Request) -> dict[str, str]:
        """The hub's public key, to put into authorized_keys."""
        require_admin(request)
        return {"public_key": hub.keys.public_key()}

    @api.get("/devices/{id}/ssh")
    async def ssh_state(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        t = target(id)
        state: dict[str, Any] = {"user": t.user, "port": t.port, "host": t.host}
        try:
            state |= {"state": "trusted", "fingerprint": await hub.keys.check_host(t)}
        except UnknownHost as e:
            state |= {"state": "unknown", "fingerprint": e.fingerprint}
        except ChangedHost as e:
            state |= {"state": "changed", "fingerprint": e.fingerprint}
        except (OSError, TimeoutError, asyncssh.Error) as e:
            state |= {"state": "unreachable", "detail": str(e) or type(e).__name__}
        return state

    @api.post("/devices/{id}/ssh/trust")
    async def trust(id: str, body: Trust, request: Request) -> dict[str, str]:
        w = require_signin(request)
        t = target(id, body.port)
        port = t.port
        try:
            await hub.keys.trust(t, body.fingerprint)
        except ChangedHost as e:
            raise HTTPException(409, str(e)) from e
        except (OSError, TimeoutError, asyncssh.Error) as e:
            raise HTTPException(502, f"cannot reach {t.host}: {e}") from e
        hub.audit.record(
            "ssh.trust",
            w.user,
            client_address(request.scope),
            device=id,
            port=port,
            fingerprint=body.fingerprint,
        )
        return {"state": "trusted"}

    @api.post("/devices/{id}/ssh/forget")
    async def forget(id: str, request: Request, port: int | None = None) -> dict[str, str]:
        w = require_signin(request)
        t = target(id, port)
        await hub.keys.forget(t)
        hub.audit.record("ssh.forget", w.user, client_address(request.scope), device=id, port=t.port)
        return {"state": "forgotten"}

    @api.websocket("/devices/{id}/terminal")
    async def terminal(ws: WebSocket, id: str, cols: int = 100, rows: int = 30) -> None:
        w: Who | None = ws.scope.get("state", {}).get("who")
        address = client_address(ws.scope)
        token = session_token(dict(ws.headers))
        if w is None or not w.is_admin or w.user is None or not same_origin(dict(ws.headers)):
            # Sign-in off, an observer, or another site's page: no shell.
            hub.audit.record("terminal.refused", w.user if w else None, address, device=id)
            await ws.close(code=4403, reason="a terminal needs a signed-in admin")
            return
        device = hub.config.devices.get(id)
        port = ssh_port(device) if device else None
        if device is None or port is None:
            await ws.close(code=4404, reason="no such device, or nothing says it has SSH")
            return
        if hub.terminals.count(w.user) >= MAX_PER_USER:
            await ws.close(code=4429, reason=f"at most {MAX_PER_USER} terminals at once")
            return
        # Registered before anything is awaited: a sign-out while connecting ends it too, and
        # parallel opens count against the limit at once.
        handle = hub.terminals.add(w.user, token, id)
        login = {"user": device.ssh.user if device.ssh else "", "way": "key"}

        async def say(message: dict[str, Any]) -> None:
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps(message))

        async def refuse(code: int, message: dict[str, Any], why: str) -> None:
            hub.audit.record("terminal.failed", w.user, address, device=id, reason=why)
            await say(message)
            await ws.close(code=code)

        def mine() -> bool:
            """This admin has their own way into the People ring (not only someone else's)."""
            me = hub.config.users.get(w.user or "")
            return me is not None and hub.vault.has_slot(PEOPLE, w.user or "", password_tag(me.password))

        def usable(e: Any) -> bool:
            # A stored login goes only to the devices it was stored for: never to another host.
            return e is not None and e.ring == PEOPLE and e.kind == "password" and id in e.devices

        def stored() -> dict[str, Any]:
            """The People ring's logins the page may offer: open ring, this admin's own slot there,
            and only logins stored for this device."""
            ring_open = hub.vault.exists and hub.vault.is_open(PEOPLE)
            entries = [
                {"id": e.id, "name": e.name, "user": e.user}
                for e in (hub.vault.entries() if ring_open and mine() else [])
                if usable(e)
            ]
            return {"vault": hub.vault.exists, "ring_open": ring_open, "stored": entries}

        async def ask(why: str, again: bool = False) -> tuple[Target, str | None] | None:
            """A login from the page: typed (with the password) or a stored one. None: it left.
            `again`: after a refused try (the page knows already what is needed)."""
            if not again:
                await say(
                    {"type": "login-needed", "why": why, "user": login["user"], "port": port, **stored()}
                )
            while True:
                try:
                    m = json.loads(await asyncio.wait_for(ws.receive_text(), LOGIN_WAIT))
                except (TimeoutError, WebSocketDisconnect, RuntimeError, ValueError):
                    return None
                if not isinstance(m, dict) or m.get("type") != "login":
                    continue
                if isinstance(m.get("entry"), str):
                    try:
                        entry = hub.vault.entry(m["entry"]) if hub.vault.exists else None
                        if not usable(entry) or not mine():
                            await say({"type": "login-failed", "message": "no stored login for this device"})
                            continue
                        password = hub.vault.get(entry.id, by=w.user)  # an admin's own action
                    except Locked as e:
                        await say({"type": "login-failed", "message": str(e)})
                        continue
                    except Exception:  # a damaged file, an entry gone meanwhile: never the secret
                        await say({"type": "login-failed", "message": "the stored login can't be read"})
                        continue
                    login.update(user=entry.user, way="stored", entry=entry.id, store=False)
                    return Target(device.host, port, entry.user), password
                user, password = str(m.get("user") or ""), m.get("password")
                if not USER.match(user) or not isinstance(password, str) or not password:
                    await say({"type": "login-failed", "message": "a user name and a password, please"})
                    continue
                login.update(user=user, way="password", store=bool(m.get("store")))
                return Target(device.host, port, user), password

        async def connect() -> asyncssh.SSHClientConnection | None:
            await ws.accept()
            why = "No ssh: login in config.yaml" if device.ssh is None else ""
            if device.ssh is None:
                # The host's key first: nobody types a password for a host not trusted yet
                # (UnknownHost / ChangedHost go to the page as with a key login).
                await hub.keys.check_host(Target(device.host, port, "root"))
            if device.ssh is not None:
                t = Target(device.host, port, device.ssh.user)
                try:
                    conn = await hub.keys.connect(t)
                except asyncssh.PermissionDenied:
                    why = f"{t.user}@{t.host} does not accept the hub's key"
                except Locked as e:
                    why = f"{e}; or log in yourself"
                    await hub.keys.check_host(t)  # as above: the host's key before a password
                else:
                    return conn
            again = False
            while True:
                if handle.stop.is_set():
                    return None
                asked = await ask(why, again) if why else None
                again = True
                if asked is None:
                    return None
                t, password = asked
                if (wait := _brake.blocked(address)) > 0:
                    await say({"type": "login-failed", "message": f"too many tries: wait {int(wait)} s"})
                    continue
                attempt = _brake.failed(address)
                try:
                    conn = await hub.keys.connect(t, password=password)
                except asyncssh.PermissionDenied:
                    why = f"{t.user} with that password was refused"
                    await say({"type": "login-failed", "message": why})
                    continue
                except BaseException:
                    # Never reached the password check (host key, unreachable): not a guess.
                    _brake.succeeded(address, attempt)
                    raise
                _brake.succeeded(address, attempt)
                try:
                    if login.get("store"):
                        note = store_login(t.user, password)
                        if note:
                            await say({"type": "note", "message": note})
                except BaseException:
                    conn.close()  # a ready login must not stay open when the page went meanwhile
                    raise
                return conn

        def store_login(user: str, password: str | None) -> str | None:
            """The typed login into the People ring (the page asked to): a note for the page."""
            if not password:
                return None
            base = f"ssh-{id}-{user}".lower().replace("_", "-")
            entry_id, n = base, 2
            while hub.vault.has(entry_id):
                entry_id, n = f"{base}-{n}", n + 1
            try:
                hub.vault.put(
                    entry_id,
                    password,
                    ring=PEOPLE,
                    kind="password",
                    name=f"{device.name or id} ({user})",
                    user=user,
                    devices=[id],  # only ever sent to this device again
                )
            except Locked as e:
                return f"not stored: {e}"
            except Exception:  # never the password in a message
                return "not stored: the key ring can't be written"
            hub.audit.record("vault.add", w.user, address, entry=entry_id, ring=PEOPLE, kind="password")
            return f"stored in the People ring as {entry_id}"

        try:
            try:
                conn = await connect()
            except UnknownHost as e:
                await refuse(
                    4001,
                    {"type": "unknown-host", "fingerprint": e.fingerprint, "port": port},
                    "unknown host key",
                )
                conn = None
            except ChangedHost as e:
                await refuse(
                    4002,
                    {"type": "changed-host", "fingerprint": e.fingerprint, "port": port},
                    "host key changed",
                )
                conn = None
            except (OSError, TimeoutError, asyncssh.Error) as e:
                message = f"cannot reach {device.host}: {e or type(e).__name__}"
                await refuse(4004, {"type": "error", "message": message}, "unreachable")
                conn = None
            if conn is not None and handle.stop.is_set():  # signed out while connecting
                conn.close()
                await refuse(4403, {"type": "error", "message": handle.reason or "signed out"}, "signed out")
                conn = None
        except BaseException:
            # The page left (or anything else) while connecting: said in the audit log before
            # anything is awaited, and the handle gone.
            hub.audit.record("terminal.failed", w.user, address, device=id, reason="ended while connecting")
            hub.terminals.remove(handle)
            raise
        if conn is None:
            hub.terminals.remove(handle)
            with contextlib.suppress(Exception):
                await ws.close()
            return

        opened = time.monotonic()
        hub.audit.record(
            "terminal.open",
            w.user,
            address,
            device=id,
            login=f"{login['user']}@{device.host}",
            way=login["way"],
            **({"entry": login["entry"]} if "entry" in login else {}),
        )
        ended = "closed"
        try:
            async with conn:
                process = await conn.create_process(
                    term_type="xterm-256color",
                    term_size=(max(10, min(cols, 500)), max(4, min(rows, 200))),
                    encoding=None,
                )

                def allowed() -> str | None:
                    """Why the terminal must end now, or None."""
                    if handle.stop.is_set():
                        return handle.reason or "signed out"
                    now = session_who(hub, token)
                    if now is None or now.user != w.user or not now.is_admin:
                        return "your session ended"
                    if time.monotonic() - opened > MAX_SECONDS:
                        return f"closed after {MAX_SECONDS // 3600} hours"
                    return None

                ended = await _bridge(ws, process, allowed, handle.stop)
                if ended == "exit":
                    await say({"type": "exit", "status": process.exit_status})
                elif ended != "closed":
                    await say({"type": "error", "message": ended})
        except (OSError, asyncssh.Error) as e:
            ended = str(e) or type(e).__name__
            await say({"type": "error", "message": ended})
        finally:
            hub.terminals.remove(handle)
            hub.audit.record(
                "terminal.close",
                w.user,
                address,
                device=id,
                seconds=round(time.monotonic() - opened),
                why=ended,
            )
            with contextlib.suppress(Exception):
                await ws.close()

    return api


async def _bridge(
    ws: WebSocket, process: asyncssh.SSHClientProcess, allowed: Callable[[], str | None], stop: asyncio.Event
) -> str:
    """Copy output to the browser and input to the shell until one side ends or the terminal is
    no longer allowed. Returns why it ended: "exit", "closed", or a reason to show."""
    last_input = time.monotonic()
    why = "closed"

    async def output() -> None:
        nonlocal why
        while True:
            data = await process.stdout.read(16384)
            if not data:
                why = "exit"
                return
            await ws.send_bytes(data)

    async def input() -> None:
        nonlocal last_input
        while True:
            try:
                message = await asyncio.wait_for(ws.receive_text(), RECHECK_EVERY)
            except TimeoutError:
                continue
            except (WebSocketDisconnect, RuntimeError):
                return
            try:
                m = json.loads(message)
            except ValueError:
                continue
            if m.get("type") == "input" and isinstance(m.get("data"), str):
                last_input = time.monotonic()  # only typing counts as use, not resizing
                process.stdin.write(m["data"].encode())
                await process.stdin.drain()
            elif m.get("type") == "resize":
                with contextlib.suppress(TypeError, ValueError, KeyError):
                    cols, rows = max(10, min(int(m["cols"]), 500)), max(4, min(int(m["rows"]), 200))
                    process.change_terminal_size(cols, rows)

    async def watch() -> None:
        """Ends the terminal when its session ends, the user loses access, or it idles."""
        nonlocal why
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), RECHECK_EVERY)
            if reason := allowed():
                why = reason
                return
            if time.monotonic() - last_input > IDLE_LIMIT:
                why = f"closed after {IDLE_LIMIT // 60} minutes without typing"
                return

    tasks = [asyncio.create_task(output()), asyncio.create_task(input()), asyncio.create_task(watch())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        process.close()
    return why
