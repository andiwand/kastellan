"""Device web pages through the hub: `<device id>.<web.domain>` forwards to the device's own web
interface, so it works unchanged (a path under the hub's name would break most device pages).

Plain ASGI in front of everything (also the Guard: a device's own /api is the device's). The
hub's session cookie stays on the hub's name; a device's name gets its own access: the device
page's link asks the hub for a one-time ticket (`router`, signed-in admins, audited), which the
device's name trades for a cookie of its own (`WEB_COOKIE`, host-only) tied to that hub session
and that device. Each request checks it again, and open streams and WebSockets re-check every
`RECHECK_EVERY` seconds, so signing out ends them.

Device pages share one site (siblings under the domain): a request one device's page sends to
another's (Sec-Fetch-Site same-site/cross-site, or an Origin not its own) is refused, so a
compromised device can't use the admin's access to another. Nothing of Kastellan's reaches the
device (its cookies and the X-Kastellan header are taken out), and a device can't set cookies
beyond its own name. The user signs in to the device as usual: Kastellan stores no device
logins. Devices' certificates aren't verified (self-signed at home, as for the checks).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import secrets
import ssl
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .auth import SESSION_COOKIE
from .auth_api import client_address, require_signin, session_who
from .config import CheckType, Device

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from .hub import Hub

log = logging.getLogger(__name__)

ENTER = "/.kastellan/enter"  # on a device's name: trades a ticket for its cookie
WEB_COOKIE = "kastellan_web"
TICKET_SECONDS = 60
RECHECK_EVERY = 15  # seconds, for open streams and WebSockets
MAX_ENTRIES = 10_000

# Never passed on: per connection, or Kastellan's own.
HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "x-kastellan",
}
WS_OWN = {"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol"}
OURS = {SESSION_COOKIE, WEB_COOKIE}
PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


class NoCookies(DefaultCookiePolicy):
    """Device cookies belong to the browser, never to the shared connection pool."""

    def set_ok(self, cookie, request) -> bool:
        return False


def new_client() -> httpx.AsyncClient:
    # No read timeout: a camera's stream or a long download goes on as long as it goes on. Each
    # stream gives its connection back when it ends (also when the browser leaves).
    return httpx.AsyncClient(
        verify=False,
        timeout=httpx.Timeout(30, read=None, pool=10),
        limits=httpx.Limits(max_connections=256, max_keepalive_connections=32),
        cookies=CookieJar(policy=NoCookies()),
    )


def label(id: str) -> str:
    """A device's name under the domain (DNS names have no `_`)."""
    return id.replace("_", "-")


def upstream(device: Device) -> str | None:
    """The device's web interface: `web:`, else its first http check's scheme://host:port."""
    if device.web:
        return device.web.rstrip("/")
    for c in device.checks:
        if c.type is CheckType.HTTP and isinstance(c.target, str):
            u = urlsplit(c.target)
            return f"{u.scheme}://{u.netloc}"
    return None


def _host(headers: dict[str, str]) -> str:
    host = headers.get("host", "").lower()
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _port(headers: dict[str, str]) -> str:
    """The port the browser used, as written in Host (`:8100`), or ""."""
    host = headers.get("host", "")
    return ":" + host.rsplit(":", 1)[1] if host.count(":") == 1 else ""


def origin_of(url: str) -> tuple[str, str, int] | None:
    """scheme, host, port (default ports filled in), or None for a relative or odd URL."""
    try:
        u = urlsplit(url)
        scheme = u.scheme.lower()
        port = u.port or PORTS.get(scheme)
    except ValueError:
        return None
    if not u.hostname or port is None:
        return None
    return scheme.replace("ws", "http"), u.hostname.lower(), port


def rebase(value: str, src: str, dst: str) -> str:
    """`value` moved from origin `src` to `dst`, only if it is exactly on `src` (not merely
    starting with the same text: 192.168.1.1 isn't 192.168.1.10 or :8443)."""
    if origin_of(value) is None or origin_of(value) != origin_of(src):
        return value
    u = urlsplit(value)
    return dst + value[len(f"{u.scheme}://{u.netloc}") :]


def without_ours(cookie: str) -> str:
    """The Cookie header without Kastellan's cookies (the device's own stay)."""
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    return "; ".join(p for p in parts if p.split("=", 1)[0].strip() not in OURS)


def host_only(set_cookie: str, insecure: bool) -> str | None:
    """A device's Set-Cookie kept on its own name: no Domain (it could reach the hub's), never
    Kastellan's cookie names; `Secure` dropped when the page is reached over http."""
    name = set_cookie.split("=", 1)[0].strip()
    if name in OURS:
        return None
    parts = [p for p in set_cookie.split(";") if p.split("=", 1)[0].strip().lower() != "domain"]
    if insecure:
        parts = [p for p in parts if p.strip().lower() != "secure"]
    return ";".join(parts)


def _cookie(headers: dict[str, str], name: str) -> str | None:
    jar = SimpleCookie()
    try:
        jar.load(headers.get("cookie", ""))
    except Exception:  # a malformed Cookie header carries no access
        return None
    return jar[name].value if name in jar else None


@dataclass
class _Ticket:
    session: str  # the hub session's token (in memory only)
    name: str  # the device's name under the domain
    expires: float


class Access:
    """Tickets (one use, a minute) and what each device-name cookie stands for: a hub session
    and one device. In memory: after a restart, the page is opened from Kastellan again."""

    def __init__(self) -> None:
        self._tickets: dict[str, _Ticket] = {}
        self._entries: dict[str, tuple[str, str]] = {}  # cookie -> (hub session token, name)

    def ticket(self, session: str, name: str) -> str:
        now = time.monotonic()
        self._tickets = {k: t for k, t in self._tickets.items() if t.expires > now}
        while len(self._tickets) >= 1000:
            del self._tickets[next(iter(self._tickets))]
        key = secrets.token_urlsafe(24)
        self._tickets[key] = _Ticket(session, name, now + TICKET_SECONDS)
        return key

    def enter(self, key: str, name: str) -> str | None:
        """A new cookie value for the ticket, once; None if unknown, used, old or another name's."""
        t = self._tickets.pop(key, None)
        if t is None or t.expires < time.monotonic() or t.name != name:
            return None
        while len(self._entries) >= MAX_ENTRIES:
            del self._entries[next(iter(self._entries))]
        value = secrets.token_urlsafe(32)
        self._entries[value] = (t.session, name)
        return value

    def session(self, value: str | None, name: str) -> str | None:
        entry = self._entries.get(value or "")
        return entry[0] if entry and entry[1] == name else None

    def forget(self, value: str | None) -> None:
        self._entries.pop(value or "", None)


def router(hub: Hub, access: Access) -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.get("/devices/{id}/web")
    async def open_web(id: str, request: Request) -> RedirectResponse:
        """The device's page through the hub: a one-time ticket, then its own name."""
        w = require_signin(request)
        web, device = hub.config.web, hub.config.devices.get(id)
        if web is None or device is None or upstream(device) is None:
            raise HTTPException(404, "no web interface for that device through Kastellan")
        key = access.ticket(request.cookies.get(SESSION_COOKIE) or "", label(id))
        headers = {k.lower(): v for k, v in request.headers.items()}
        where = f"{_scheme(request.scope, headers)}://{label(id)}.{web.domain}{_port(headers)}"
        hub.audit.record("web.open", w.user, client_address(request.scope), device=id)
        return RedirectResponse(
            f"{where}{ENTER}?ticket={key}", status_code=303, headers={"Referrer-Policy": "no-referrer"}
        )

    return api


class WebProxy:
    """ASGI middleware: requests for `<id>.<web.domain>` go to that device."""

    def __init__(self, app: ASGIApp, hub: Hub, access: Access) -> None:
        self.app, self.hub, self.access = app, hub, access
        self._client: httpx.AsyncClient | None = None

    def allowed(self, value: str | None, name: str) -> bool:
        """The cookie still stands for a signed-in admin's session, for this device's name."""
        token = self.access.session(value, name)
        w = session_who(self.hub, token) if token else None
        return w is not None and w.is_admin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        web = self.hub.config.web
        if scope["type"] not in ("http", "websocket") or web is None:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        host = _host(headers)
        if not host.endswith("." + web.domain):
            await self.app(scope, receive, send)
            return
        scheme = _scheme(scope, headers)
        here = f"{scheme}://{host}{_port(headers)}"
        back = f"{scheme}://{web.domain}{_port(headers)}/"
        if self.hub.locked:
            await _deny(scope, send, 503, "Kastellan is locked until config.yaml is fixed.")
            return
        if not self.hub.config.users:
            await _deny(scope, send, 403, "Turn on sign-in in Kastellan first (add a user to config.yaml).")
            return
        name = host[: -len(web.domain) - 1]
        if scope["type"] == "http" and scope["path"] == ENTER:
            await self._enter(scope, send, name, here, back)
            return
        value = _cookie(headers, WEB_COOKIE)
        if not self.allowed(value, name):
            if self.access.session(value, name):  # its session ended: the cookie is done too
                self.access.forget(value)
            await _deny(scope, send, 401, f"Open this page from its device in Kastellan: {back}")
            return
        if _from_elsewhere(scope, headers, here):
            await _deny(scope, send, 403, f"Asked for by another page; open it from Kastellan: {back}")
            return
        # Only now which devices exist: nobody without access learns their names.
        id = next((i for i in self.hub.config.devices if label(i) == name), None)
        device = self.hub.config.devices.get(id) if id else None
        target = upstream(device) if device else None
        if target is None:
            await _deny(scope, send, 404, "Kastellan knows no web interface by that name.")
            return

        def still() -> bool:
            return self.allowed(value, name)

        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, target, headers, here, still)
        else:
            await self._http(scope, receive, send, target, headers, here, still)

    async def _enter(self, scope: Scope, send: Send, name: str, here: str, back: str) -> None:
        query = parse_qs((scope.get("query_string") or b"").decode(errors="replace"))
        value = self.access.enter((query.get("ticket") or [""])[0], name)
        if value is None:
            await _deny(
                scope, send, 401, f"That link is used or too old; open the page from Kastellan: {back}"
            )
            return
        secure = "; Secure" if here.startswith("https:") else ""
        cookie = f"{WEB_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict{secure}"
        # A page that moves on by itself, not a redirect: the next request is then the device
        # name's own (same-origin), not one that came from the hub's name.
        body = (
            b"<!doctype html><meta charset=utf-8>"
            b'<meta http-equiv="refresh" content="0;url=/"><title>Kastellan</title>\n'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"set-cookie", cookie.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _forwarded(self, scope: Scope, headers: dict[str, str], target: str, here: str) -> dict[str, str]:
        out = {k: v for k, v in headers.items() if k not in HOP}
        if "cookie" in out:
            out["cookie"] = without_ours(out["cookie"])
            if not out["cookie"]:
                del out["cookie"]
        # The device's own CSRF checks compare these with its own address.
        for key in ("origin", "referer"):
            if key in out:
                out[key] = rebase(out[key], here, target)
        out["x-forwarded-for"] = client_address(scope)
        out["x-forwarded-host"] = _host(headers)
        out["x-forwarded-proto"] = here.split(":", 1)[0]
        return out

    async def _http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        target: str,
        headers: dict[str, str],
        here: str,
        still: Callable[[], bool],
    ) -> None:
        if self._client is None:
            self._client = new_client()

        # The browser's leaving is watched only once its body is all read: receive() has one
        # reader at a time.
        read_all = asyncio.Event()

        async def body() -> AsyncIterator[bytes]:
            try:
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    yield message.get("body", b"")
                    if not message.get("more_body"):
                        return
            finally:
                read_all.set()

        # A body only when there is one (a GET sent chunked puts some devices off).
        has_body = headers.get("content-length", "0") != "0" or "transfer-encoding" in headers
        if not has_body:
            read_all.set()
        # Construct directly: client defaults (especially another browser's cookies) must
        # never be merged into a proxied request.
        request = httpx.Request(
            scope["method"],
            target + _path(scope),
            headers=self._forwarded(scope, headers, target, here),
            content=body() if has_body else None,
        )
        response: httpx.Response | None = None
        started = finished = False

        async def exchange() -> None:
            nonlocal response, started, finished
            try:
                response = await self._client.send(request, stream=True)  # type: ignore[union-attr]
            except httpx.HTTPError as e:
                started = True
                await _deny(scope, send, 502, f"The device doesn't answer: {type(e).__name__}.")
                finished = True
                return
            out: list[tuple[bytes, bytes]] = []
            for k, v in response.headers.multi_items():
                key = k.lower()
                if key in HOP:
                    continue
                if key == "location":
                    v = rebase(v, target, here)
                if key == "set-cookie":
                    kept = host_only(v, here.startswith("http:"))
                    if kept is None:
                        continue
                    v = kept
                out.append((key.encode(), v.encode()))
            started = True
            await send({"type": "http.response.start", "status": response.status_code, "headers": out})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
            finished = True

        async def gone() -> None:  # the browser left (a closed tab, or a device that never answers)
            await read_all.wait()
            while (await receive())["type"] != "http.disconnect":
                pass

        # Also while waiting for the device's first answer: a hung device must not hold the
        # connection (nor answer after the sign-in ended).
        tasks = [asyncio.ensure_future(f()) for f in (exchange, gone, lambda: _watch(still))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            _end(tasks)
            if response is not None:
                await response.aclose()  # its connection goes back to the pool
            if not finished:
                with contextlib.suppress(Exception):
                    if not started:
                        await send({"type": "http.response.start", "status": 504, "headers": []})
                    await send({"type": "http.response.body", "body": b""})

    async def _websocket(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        target: str,
        headers: dict[str, str],
        here: str,
        still: Callable[[], bool],
    ) -> None:
        if (await receive())["type"] != "websocket.connect":
            return
        url = "ws" + target[4:] + _path(scope)
        forwarded = {
            k: v for k, v in self._forwarded(scope, headers, target, here).items() if k not in WS_OWN
        }
        protocols = [p.strip() for p in headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
        context = None
        if url.startswith("wss:"):
            context = ssl.create_default_context()
            context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
        try:
            upstream_ws = await ws_connect(
                url,
                additional_headers=forwarded,
                subprotocols=protocols or None,  # type: ignore[arg-type]
                ssl=context,
                open_timeout=10,
                max_size=None,
                compression=None,
                user_agent_header=None,  # the browser's, among the forwarded headers
            )
        except Exception as e:  # refused, timed out, a handshake it rejects, ...
            log.info("web page websocket to %s failed: %s", target, e)
            await send({"type": "websocket.close", "code": 1011})
            return
        await send({"type": "websocket.accept", "subprotocol": upstream_ws.subprotocol})

        async def to_device() -> None:
            while True:
                message = await receive()
                if message["type"] == "websocket.disconnect":
                    return
                data = message.get("text") if message.get("text") is not None else message.get("bytes")
                if data is not None:
                    await upstream_ws.send(data)

        async def to_browser() -> None:
            with contextlib.suppress(ConnectionClosed):
                async for data in upstream_ws:
                    key = "text" if isinstance(data, str) else "bytes"
                    await send({"type": "websocket.send", key: data})

        tasks = [asyncio.ensure_future(f()) for f in (to_device, to_browser, lambda: _watch(still))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            _end(tasks)
            await upstream_ws.close()
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 1000})


def _end(tasks: list[asyncio.Future[None]]) -> None:
    """Stop the other directions; not waited for (this handler may itself be being cancelled),
    their outcome only collected."""
    for t in tasks:
        t.cancel()
        t.add_done_callback(lambda t: t.cancelled() or t.exception())


async def _watch(still: Callable[[], bool]) -> None:
    """Returns once the access behind an open stream or WebSocket is gone (signed out, no
    longer an admin): checked every RECHECK_EVERY seconds."""
    while True:
        await asyncio.sleep(RECHECK_EVERY)
        if not still():
            return


def _from_elsewhere(scope: Scope, headers: dict[str, str], here: str) -> bool:
    """Sent by another page than this device name's own (another device's, say): refused."""
    origin = headers.get("origin")
    if scope["type"] == "websocket":  # no CORS for WebSockets: the Origin must be its own
        return origin is None or origin_of(origin) != origin_of(here)
    if headers.get("sec-fetch-site") in ("same-site", "cross-site"):
        return True
    return origin is not None and origin_of(origin) != origin_of(here)


def _path(scope: Scope) -> str:
    """The path and query as the browser sent them."""
    path = (scope.get("raw_path") or scope["path"].encode()).decode(errors="replace")
    query = (scope.get("query_string") or b"").decode(errors="replace")
    return path + ("?" + query if query else "")


def _scheme(scope: Scope, headers: dict[str, str]) -> str:
    """How the browser reached the hub: the HTTPS proxy in front says so."""
    proto = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto if proto in ("http", "https") else scope.get("scheme", "http").replace("ws", "http")


async def _deny(scope: Scope, send: Send, status: int, message: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 4403, "reason": message[:120]})
        return
    text = re.sub(r"[<>&]", "", message)
    body = f"<!doctype html><meta charset=utf-8><title>Kastellan</title><p>{text}</p>\n".encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
