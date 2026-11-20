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
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .auth_api import Who, client_address, require_admin, require_signin, same_origin
from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

IDLE_LIMIT = 30 * 60  # a terminal nobody typed in for this long closes


class Trust(BaseModel):
    fingerprint: str


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
        t = target(id)
        try:
            await hub.keys.trust(t, body.fingerprint)
        except ChangedHost as e:
            raise HTTPException(409, str(e)) from e
        except (OSError, TimeoutError, asyncssh.Error) as e:
            raise HTTPException(502, f"cannot reach {t.host}: {e}") from e
        hub.audit.record(
            "ssh.trust", w.user, client_address(request.scope), device=id, fingerprint=body.fingerprint
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
        if w is None or not w.is_admin or w.user is None or not same_origin(dict(ws.headers)):
            # Sign-in off, an observer, or another site's page: no shell.
            await ws.close(code=4403, reason="a terminal needs a signed-in admin")
            return
        device = hub.config.devices.get(id)
        if device is None or device.ssh is None:
            await ws.close(code=4404, reason="no such device, or it has no ssh: in config.yaml")
            return
        await ws.accept()
        t = Target(device.host, device.ssh.port, device.ssh.user)
        address = client_address(ws.scope)

        async def say(message: dict[str, Any]) -> None:
            with contextlib.suppress(Exception):
                await ws.send_text(json.dumps(message))

        try:
            conn = await hub.keys.connect(t)
        except UnknownHost as e:
            await say({"type": "unknown-host", "fingerprint": e.fingerprint})
            await ws.close(code=4001)
            return
        except ChangedHost as e:
            await say({"type": "changed-host", "fingerprint": e.fingerprint})
            await ws.close(code=4002)
            return
        except asyncssh.PermissionDenied:
            await say({"type": "error", "message": f"{t.user}@{t.host} does not accept the hub's key"})
            await ws.close(code=4003)
            return
        except (OSError, TimeoutError, asyncssh.Error) as e:
            await say({"type": "error", "message": f"cannot reach {t.host}: {e or type(e).__name__}"})
            await ws.close(code=4004)
            return

        opened = time.monotonic()
        hub.audit.record("terminal.open", w.user, address, device=id, login=f"{t.user}@{t.host}")
        try:
            async with conn:
                process = await conn.create_process(
                    term_type="xterm-256color",
                    term_size=(max(10, min(cols, 500)), max(4, min(rows, 200))),
                    encoding=None,
                )
                await _bridge(ws, process)
                await say({"type": "exit", "status": process.exit_status})
        except (OSError, asyncssh.Error) as e:
            await say({"type": "error", "message": str(e) or type(e).__name__})
        finally:
            hub.audit.record(
                "terminal.close", w.user, address, device=id, seconds=round(time.monotonic() - opened)
            )
            with contextlib.suppress(Exception):
                await ws.close()

    return api


async def _bridge(ws: WebSocket, process: asyncssh.SSHClientProcess) -> None:
    """Copy output to the browser and input to the shell until either side ends."""
    last_input = time.monotonic()

    async def output() -> None:
        while True:
            data = await process.stdout.read(16384)
            if not data:
                return
            await ws.send_bytes(data)

    async def input() -> None:
        nonlocal last_input
        while True:
            try:
                message = await asyncio.wait_for(ws.receive_text(), 60)
            except TimeoutError:
                if time.monotonic() - last_input > IDLE_LIMIT:
                    await ws.send_text(
                        json.dumps({"type": "error", "message": "closed after 30 minutes idle"})
                    )
                    return
                continue
            except WebSocketDisconnect:
                return
            try:
                m = json.loads(message)
            except ValueError:
                continue
            if m.get("type") == "input" and isinstance(m.get("data"), str):
                last_input = time.monotonic()
                process.stdin.write(m["data"].encode())
            elif m.get("type") == "resize":
                with contextlib.suppress(TypeError, ValueError):
                    process.change_terminal_size(
                        max(10, min(int(m["cols"]), 500)), max(4, min(int(m["rows"]), 200))
                    )

    tasks = [asyncio.create_task(output()), asyncio.create_task(input())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        process.close()
