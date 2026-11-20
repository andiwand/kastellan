"""Remote desktop in the browser: guacd does RDP/VNC, the hub relays its protocol to the page.

    POST /api/devices/{id}/desktop/ticket  {"username": ..., "password": ...}  → {"ticket": ...}
    ws   /api/devices/{id}/desktop?ticket=...&width=1280&height=800&dpi=96   (subprotocol guacamole)

The desktop's password is never stored and never in a URL (URLs end up in logs): the page posts
it once and gets a ticket for it, good for one connection within a minute, only for the same
session. The ticket lives in `hub.tickets`; it expires on a timer and with a sign-out. The
password is handed to guacd in the handshake and dropped right after.

Same rules as the terminal: a signed-in admin, the hub's own page, closed when the session ends
or the user's access changes, after 30 minutes without input, after 8 hours in any case. The
browser may only send what a screen needs (mouse, keys, size); no clipboard, files or drives.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from . import guac
from .auth_api import Who, client_address, require_signin, same_origin, session_token, session_who
from .terminals import token_key

if TYPE_CHECKING:
    from .config import Device
    from .hub import Hub

log = logging.getLogger(__name__)

TICKET_SECONDS = 60
MAX_TICKETS = 20
MAX_SECONDS = 8 * 3600
IDLE_LIMIT = 30 * 60
RECHECK_EVERY = 15
MAX_PER_USER = 4
DEFAULT_PORTS = {"rdp": 3389, "vnc": 5900}
# What the page may send to guacd: input and the screen size; the tunnel's own pings are answered
# by the hub. Nothing else (no clipboard, files, pipes or arguments) reaches guacd.
FROM_BROWSER = {"mouse", "key", "size", "sync", "ack", "nop", "disconnect"}
MAX_SIZE = (7680, 4320)


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
    expires: float = field(default_factory=lambda: time.monotonic() + TICKET_SECONDS)


class Tickets:
    """One-time tickets for desktop connections, kept only as long as they're good."""

    def __init__(self) -> None:
        self._tickets: dict[str, Ticket] = {}

    def add(self, ticket: Ticket) -> str:
        self.expire()
        mine = [k for k, t in self._tickets.items() if t.session == ticket.session]
        for key in mine[: max(0, len(mine) - MAX_TICKETS + 1)]:  # a session holds a few at most
            del self._tickets[key]
        key = secrets.token_urlsafe(24)
        self._tickets[key] = ticket
        return key

    def take(self, key: str) -> Ticket | None:
        """The ticket, once; None if unknown or expired."""
        self.expire()
        return self._tickets.pop(key, None)

    def expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, t in self._tickets.items() if t.expires < now]:
            del self._tickets[key]

    def drop(self, *, user: str | None = None, session: str | None = None) -> None:
        for key in [
            k
            for k, t in self._tickets.items()
            if (user and t.user == user) or (session and t.session == session)
        ]:
            del self._tickets[key]


def guacd_params(device: Device, credentials: Credentials) -> dict[str, str]:
    d = device.desktop
    assert d is not None
    return {
        "hostname": device.host,
        "port": str(d.port or DEFAULT_PORTS[d.protocol]),
        "username": credentials.username,
        "password": credentials.password,
        "domain": credentials.domain,
        # RDP
        "security": d.security,
        "ignore-cert": "true" if d.ignore_cert else "false",
        "resize-method": "display-update",
        "enable-font-smoothing": "true",
        # Never let the remote side reach the hub's files or printers, nor the clipboard.
        "enable-drive": "false",
        "enable-printing": "false",
        "disable-copy": "true",
        "disable-paste": "true",
    }


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.post("/devices/{id}/desktop/ticket")
    def ticket(id: str, body: Credentials, request: Request) -> dict[str, str]:
        w = require_signin(request)
        device = hub.config.devices.get(id)
        if device is None or device.desktop is None:
            raise HTTPException(404, "no such device, or it has no desktop: in config.yaml")
        session = token_key(request.cookies.get("kastellan_session"))
        return {"ticket": hub.tickets.add(Ticket(w.user or "", session, id, body))}

    @api.websocket("/devices/{id}/desktop")
    async def desktop(
        ws: WebSocket, id: str, ticket: str = "", width: int = 1280, height: int = 800, dpi: int = 96
    ):
        w: Who | None = ws.scope.get("state", {}).get("who")
        address = client_address(ws.scope)
        headers = dict(ws.headers)
        token = session_token(headers)
        t = hub.tickets.take(ticket)  # one use, whatever happens next
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
        # Registered before anything is awaited: a sign-out during the handshake ends it too, and
        # parallel opens count against the limit at once.
        handle = hub.terminals.add(w.user, token, id)
        opened = time.monotonic()
        ended = "closed"
        writer: asyncio.StreamWriter | None = None

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
            await ws.accept(subprotocol="guacamole")
            host, _, port = hub.settings.guacd.rpartition(":")
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host or "127.0.0.1", int(port)), 10
                )
            except (OSError, TimeoutError, ValueError) as e:
                ended = "guacd unreachable"
                await _error(ws, f"guacd is not running at {hub.settings.guacd} ({e or type(e).__name__})")
                return
            guacd = guac.Reader(reader)
            size = (
                max(320, min(width, MAX_SIZE[0])),
                max(240, min(height, MAX_SIZE[1])),
                max(48, min(dpi, 300)),
            )
            try:
                # The password exists only inside this call; the ticket's copy goes first.
                credentials, t.credentials = t.credentials, Credentials()
                await guac.handshake(
                    guacd, writer, device.desktop.protocol, guacd_params(device, credentials), size
                )
            except (guac.ProtocolError, OSError, TimeoutError) as e:
                ended = f"handshake failed: {str(e)[:200]}"
                await _error(ws, f"cannot connect to {device.host}: {e}")
                return
            finally:
                del credentials
            if reason := allowed():  # signed out while connecting
                ended = reason
                await _error(ws, reason)
                return
            hub.audit.record("desktop.open", w.user, address, device=id, protocol=device.desktop.protocol)
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
            if writer is not None:
                writer.close()
            with contextlib.suppress(Exception):
                await ws.close()

    return api


async def _error(ws: WebSocket, message: str) -> None:
    """Tell the page in the protocol's own terms (an `error` instruction), then close."""
    with contextlib.suppress(Exception):
        await ws.send_text(guac.encode("error", message, "512"))
        await ws.close()


def _from_browser(instruction: str) -> str | None:
    """What may go on to guacd: only FROM_BROWSER, with a sane screen size; None drops it."""
    elements = guac.decode(instruction)
    if elements[0] not in FROM_BROWSER:
        return None
    if elements[0] == "size":
        try:
            w, h = int(elements[1]), int(elements[2])
        except (IndexError, ValueError):
            return None
        w, h = max(320, min(w, MAX_SIZE[0])), max(240, min(h, MAX_SIZE[1]))
        return guac.encode("size", str(w), str(h), *elements[3:4])
    return instruction


async def _relay(
    ws: WebSocket, guacd: guac.Reader, writer: asyncio.StreamWriter, allowed: Any, stop: asyncio.Event
) -> str:
    why = "closed"
    last_input = time.monotonic()

    async def down() -> None:
        """guacd → browser, whole instructions only, batched, lengths as the browser counts."""
        nonlocal why
        while True:
            instruction = await guacd.instruction()
            if not instruction:
                why = "ended"
                return
            batch = [guac.for_browser(instruction)]
            while len(batch) < 64 and guac.complete(guacd.buffer) is not None:
                batch.append(guac.for_browser(await guacd.instruction()))
            await ws.send_text("".join(batch))

    async def up() -> None:
        """browser → guacd, filtered; the tunnel's own pings are answered here."""
        nonlocal last_input
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
                if instruction.startswith(("5.mouse,", "3.key,")):
                    last_input = time.monotonic()
                if (allowed_instruction := _from_browser(instruction)) is not None:
                    writer.write(allowed_instruction.encode())
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
            if time.monotonic() - last_input > IDLE_LIMIT:
                why = f"closed after {IDLE_LIMIT // 60} minutes without input"
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
