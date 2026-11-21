"""The terminal in the browser: a WebSocket per session, bridged to an SSH shell on the device.

    ws /api/devices/{id}/terminal?cols=120&rows=32

Browser → hub: text frames with JSON, {"type": "input", "data": "..."} and
{"type": "resize", "cols": c, "rows": r}. Hub → browser: binary frames with the terminal's
output, and text frames with JSON for what isn't output ({"type": "unknown-host", ...},
{"type": "error", ...}, {"type": "exit", ...}).

Only signed-in admins, only to devices with `ssh:` in config.yaml, only to hosts whose key an
admin trusted (ssh.py). The audit log gets who opened which terminal and how long it stayed
open; never what was typed, which can contain passwords.
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

from .auth_api import (
    Who,
    client_address,
    require_admin,
    require_signin,
    same_origin,
    session_token,
    session_who,
)
from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

IDLE_LIMIT = 30 * 60  # a terminal nobody typed in for this long closes
MAX_SECONDS = 8 * 3600  # however busy, a terminal ends after this long
MAX_PER_USER = 4
RECHECK_EVERY = 15  # seconds between checks that the session is still good


class Trust(BaseModel):
    fingerprint: str
    # The port a setup logs in on, for a device without `ssh:` yet; else the ssh: port.
    port: int | None = Field(default=None, ge=1, le=65535)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    def target(id: str) -> Target:
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        if device.ssh is None:
            raise HTTPException(400, f"{id} has no `ssh:` in config.yaml")
        return Target(device.host, device.ssh.port, device.ssh.user)

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
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        # A host key belongs to the address and port, not to a login (setups trust it too).
        port = body.port or (device.ssh.port if device.ssh else 22)
        t = Target(device.host, port, device.ssh.user if device.ssh else "root")
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
    async def forget(id: str, request: Request) -> dict[str, str]:
        w = require_signin(request)
        t = target(id)
        await hub.keys.forget(t)
        hub.audit.record("ssh.forget", w.user, client_address(request.scope), device=id)
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
        if device is None or device.ssh is None:
            await ws.close(code=4404, reason="no such device, or it has no ssh: in config.yaml")
            return
        if hub.terminals.count(w.user) >= MAX_PER_USER:
            await ws.close(code=4429, reason=f"at most {MAX_PER_USER} terminals at once")
            return
        # Registered before anything is awaited: a sign-out while connecting ends it too, and
        # parallel opens count against the limit at once.
        handle = hub.terminals.add(w.user, token, id)
        t = Target(device.host, device.ssh.port, device.ssh.user)

        async def say(message: dict[str, Any]) -> None:
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps(message))

        async def refuse(code: int, message: dict[str, Any], why: str) -> None:
            hub.audit.record("terminal.failed", w.user, address, device=id, reason=why)
            await say(message)
            await ws.close(code=code)

        async def connect() -> asyncssh.SSHClientConnection | None:
            await ws.accept()
            try:
                conn = await hub.keys.connect(t)
            except UnknownHost as e:
                await refuse(4001, {"type": "unknown-host", "fingerprint": e.fingerprint}, "unknown host key")
                return None
            except ChangedHost as e:
                await refuse(4002, {"type": "changed-host", "fingerprint": e.fingerprint}, "host key changed")
                return None
            except asyncssh.PermissionDenied:
                message = f"{t.user}@{t.host} does not accept the hub's key"
                await refuse(4003, {"type": "error", "message": message}, "key not accepted")
                return None
            except (OSError, TimeoutError, asyncssh.Error) as e:
                message = f"cannot reach {t.host}: {e or type(e).__name__}"
                await refuse(4004, {"type": "error", "message": message}, "unreachable")
                return None
            if handle.stop.is_set():  # signed out while connecting
                conn.close()
                await refuse(4403, {"type": "error", "message": handle.reason or "signed out"}, "signed out")
                return None
            return conn

        try:
            conn = await connect()
        except BaseException:
            hub.terminals.remove(handle)
            raise
        if conn is None:
            hub.terminals.remove(handle)
            return

        opened = time.monotonic()
        hub.audit.record("terminal.open", w.user, address, device=id, login=f"{t.user}@{t.host}")
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
