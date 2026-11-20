"""Remote desktop in the browser: guacd does RDP/VNC, the hub relays its protocol to the page.

    POST /api/devices/{id}/desktop/ticket  {"username": ..., "password": ...}  → {"ticket": ...}
    ws   /api/devices/{id}/desktop?ticket=...&width=1280&height=800&dpi=96   (subprotocol guacamole)

The desktop's password is never stored and never in a URL (URLs end up in logs): the page posts
it once and gets a ticket for it, good for one connection within a minute, only for the same
session. The WebSocket then carries just the ticket.

Same rules as the terminal: a signed-in admin, the hub's own page, closed when the session ends
or the user's access changes, at most 8 hours, audit log for open/close/refusals (never the
password or what happens on the screen).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import guac
from .auth_api import Who, client_address, require_signin, same_origin, session_token, session_who
from .terminals import token_key

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)

TICKET_SECONDS = 60
MAX_SECONDS = 8 * 3600
RECHECK_EVERY = 15
MAX_PER_USER = 4
DEFAULT_PORTS = {"rdp": 3389, "vnc": 5900}


class Credentials(BaseModel):
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=1024)
    domain: str = Field(default="", max_length=256)


@dataclass
class Ticket:
    user: str
    session: str
    device: str
    credentials: Credentials
    expires: float


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")
    tickets: dict[str, Ticket] = {}

    def forget_expired() -> None:
        now = time.monotonic()
        for key in [k for k, t in tickets.items() if t.expires < now]:
            del tickets[key]

    @api.post("/devices/{id}/desktop/ticket")
    def ticket(id: str, body: Credentials, request: Request) -> dict[str, str]:
        w = require_signin(request)
        device = hub.config.devices.get(id)
        if device is None or device.desktop is None:
            raise HTTPException(404, "no such device, or it has no desktop: in config.yaml")
        forget_expired()
        key = secrets.token_urlsafe(24)
        tickets[key] = Ticket(
            w.user or "",
            token_key(request.cookies.get("kastellan_session")),
            id,
            body,
            time.monotonic() + TICKET_SECONDS,
        )
        return {"ticket": key}

    @api.websocket("/devices/{id}/desktop")
    async def desktop(
        ws: WebSocket, id: str, ticket: str = "", width: int = 1280, height: int = 800, dpi: int = 96
    ):
        w: Who | None = ws.scope.get("state", {}).get("who")
        address = client_address(ws.scope)
        headers = dict(ws.headers)
        token = session_token(headers)
        forget_expired()
        t = tickets.pop(ticket, None)  # one use, whatever happens next
        if (
            w is None
            or not w.is_admin
            or w.user is None
            or not same_origin(headers)
            or t is None
            or t.user != w.user
            or t.session != token_key(token)
            or t.device != id
        ):
            hub.audit.record("desktop.refused", w.user if w else None, address, device=id)
            await ws.close(code=4403, reason="a remote desktop needs a signed-in admin and a fresh ticket")
            return
        device = hub.config.devices.get(id)
        if device is None or device.desktop is None:
            await ws.close(code=4404, reason="no such device, or it has no desktop: in config.yaml")
            return
        if hub.terminals.count(w.user) >= MAX_PER_USER:
            await ws.close(code=4429, reason=f"at most {MAX_PER_USER} sessions at once")
            return
        await ws.accept(subprotocol="guacamole")

        d = device.desktop
        params = {
            "hostname": device.host,
            "port": str(d.port or DEFAULT_PORTS[d.protocol]),
            "username": t.credentials.username,
            "password": t.credentials.password,
            "domain": t.credentials.domain,
            # RDP
            "security": d.security,
            "ignore-cert": "true" if d.ignore_cert else "false",
            "resize-method": "display-update",
            "enable-font-smoothing": "true",
            # Never let the remote side reach the hub's files, printers or sound devices.
            "enable-drive": "false",
            "enable-printing": "false",
            "disable-copy": "false",
            "disable-paste": "false",
        }
        size = (max(320, min(width, 7680)), max(240, min(height, 4320)), max(48, min(dpi, 300)))
        host, _, port = hub.settings.guacd.rpartition(":")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host or "127.0.0.1", int(port)), 10
            )
        except (OSError, TimeoutError, ValueError) as e:
            hub.audit.record("desktop.failed", w.user, address, device=id, reason="guacd unreachable")
            await _error(ws, f"guacd is not running at {hub.settings.guacd} ({e or type(e).__name__})")
            return
        guacd = guac.Reader(reader)
        try:
            await guac.handshake(guacd, writer, d.protocol, params, size)
        except (guac.ProtocolError, OSError, TimeoutError) as e:
            hub.audit.record("desktop.failed", w.user, address, device=id, reason=str(e)[:200])
            await _error(ws, f"cannot connect to {device.host}: {e}")
            writer.close()
            return
        finally:
            t.credentials = Credentials()  # the password is not kept a moment longer than needed

        handle = hub.terminals.add(w.user, token, id)
        opened = time.monotonic()
        hub.audit.record("desktop.open", w.user, address, device=id, protocol=d.protocol)
        ended = "closed"

        def allowed() -> str | None:
            if handle.stop.is_set():
                return handle.reason or "signed out"
            now = session_who(hub, token)
            if now is None or now.user != w.user or not now.is_admin:
                return "your session ended"
            if time.monotonic() - opened > MAX_SECONDS:
                return "closed after 8 hours"
            return None

        try:
            # The tunnel's first message names it (guacamole-common-js waits for this).
            await ws.send_text(guac.encode(guac.INTERNAL, secrets.token_hex(18)))
            ended = await _relay(ws, guacd, writer, allowed, handle.stop)
            if ended not in ("closed", "ended"):
                await _error(ws, ended)
        finally:
            # Everything that must happen comes before the first await: when the handler is being
            # cancelled (the page went away, the server stops), an await here would end it early.
            hub.terminals.remove(handle)
            hub.audit.record(
                "desktop.close",
                w.user,
                address,
                device=id,
                seconds=round(time.monotonic() - opened),
                why=ended,
            )
            writer.close()
            with contextlib.suppress(Exception):
                await ws.close()

    return api


async def _error(ws: WebSocket, message: str) -> None:
    """Tell the page in the protocol's own terms (an `error` instruction), then close."""
    with contextlib.suppress(Exception):
        await ws.send_text(guac.encode("error", message, "512"))
        await ws.close()


async def _relay(
    ws: WebSocket, guacd: guac.Reader, writer: asyncio.StreamWriter, allowed: Any, stop: asyncio.Event
) -> str:
    why = "closed"

    async def down() -> None:
        """guacd → browser, whole instructions only, batched."""
        nonlocal why
        while True:
            instruction = await guacd.instruction()
            if not instruction:
                why = "ended"
                return
            batch = [instruction]
            while len(batch) < 64 and guac.complete(guacd.buffer) is not None:
                batch.append(await guacd.instruction())
            await ws.send_text("".join(batch))

    async def up() -> None:
        """browser → guacd; the tunnel's own instructions (ping) are answered here."""
        buffer = ""
        while True:
            try:
                buffer += await ws.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                return
            while (end := guac.complete(buffer)) is not None:
                instruction, buffer = buffer[:end], buffer[end:]
                if instruction.startswith("0.,"):
                    await ws.send_text(instruction)  # ping: echo, as Guacamole's own server does
                    continue
                writer.write(instruction.encode())
            await writer.drain()
            if len(buffer) > 1024 * 1024:
                return

    async def watch() -> None:
        nonlocal why
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), RECHECK_EVERY)
            if reason := allowed():
                why = reason
                return

    tasks = [asyncio.create_task(down()), asyncio.create_task(up()), asyncio.create_task(watch())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() and not isinstance(task.exception(), WebSocketDisconnect):
                why = f"the connection broke: {task.exception()}"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return why
