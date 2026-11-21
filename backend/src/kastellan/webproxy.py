"""Device web pages through the hub: `<device id>.<web.domain>` forwards to the device's own web
interface, so it works unchanged (a path under the hub's name would break most device pages).

Plain ASGI in front of everything (also the Guard: a device's own /api is the device's). Only a
signed-in admin gets through, and nothing of Kastellan's reaches the device: its session cookie
and the X-Kastellan header are taken out, and the device can't set cookies for the hub's name
(their Domain goes; host-only, they stay on the device's name). The user signs in to the device
as usual: Kastellan stores no device logins. Devices' certificates aren't verified (they are
self-signed at home, as for the checks).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import ssl
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from .auth import SESSION_COOKIE
from .auth_api import client_address, session_token, session_who
from .config import CheckType, Device
from .terminals import token_key

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from .hub import Hub

log = logging.getLogger(__name__)

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


def new_client() -> httpx.AsyncClient:
    # No read timeout: a camera's stream or a long download goes on as long as it goes on.
    return httpx.AsyncClient(verify=False, timeout=httpx.Timeout(30, read=None))


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


def without_session(cookie: str) -> str:
    """The Cookie header without Kastellan's session (the device's own cookies stay)."""
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    return "; ".join(p for p in parts if p.split("=", 1)[0].strip() != SESSION_COOKIE)


def host_only(set_cookie: str, insecure: bool) -> str | None:
    """A device's Set-Cookie kept on its own name: no Domain (it could reach the hub's), never
    Kastellan's cookie name; `Secure` dropped when the page is reached over http."""
    name = set_cookie.split("=", 1)[0].strip()
    if name == SESSION_COOKIE:
        return None
    parts = [p for p in set_cookie.split(";") if p.split("=", 1)[0].strip().lower() != "domain"]
    if insecure:
        parts = [p for p in parts if p.strip().lower() != "secure"]
    return ";".join(parts)


class WebProxy:
    """ASGI middleware: requests for `<id>.<web.domain>` go to that device."""

    def __init__(self, app: ASGIApp, hub: Hub) -> None:
        self.app, self.hub = app, hub
        self._client: httpx.AsyncClient | None = None
        self._opened: set[tuple[str, str]] = set()  # (session, device) audited once

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
        if self.hub.locked:
            await _deny(scope, send, 503, "Kastellan is locked until config.yaml is fixed.")
            return
        if not self.hub.config.users:
            await _deny(scope, send, 403, "Turn on sign-in in Kastellan first (add a user to config.yaml).")
            return
        token = session_token(headers)
        w = session_who(self.hub, token)
        if w is None:
            await _deny(
                scope, send, 401, f"Sign in to Kastellan first: {_scheme(scope, headers)}://{web.domain}/"
            )
            return
        if not w.is_admin:
            await _deny(scope, send, 403, "Only Kastellan's admins open device pages.")
            return
        # Only now which devices exist: nobody signed out learns their names.
        name = host[: -len(web.domain) - 1]
        id = next((i for i in self.hub.config.devices if label(i) == name), None)
        device = self.hub.config.devices.get(id) if id else None
        target = upstream(device) if device else None
        if target is None or id is None:
            await _deny(scope, send, 404, "Kastellan knows no web interface by that name.")
            return
        opened = (token_key(token), id)
        if opened not in self._opened:
            if len(self._opened) > 10_000:
                self._opened.clear()
            self._opened.add(opened)
            self.hub.audit.record("web.open", w.user, client_address(scope), device=id)
        here = f"{_scheme(scope, headers)}://{host}"
        if scope["type"] == "websocket":
            await self._websocket(scope, receive, send, target, headers, here)
        else:
            await self._http(scope, receive, send, target, headers, here)

    def _forwarded(self, scope: Scope, headers: dict[str, str], target: str, here: str) -> dict[str, str]:
        out = {k: v for k, v in headers.items() if k not in HOP}
        if "cookie" in out:
            out["cookie"] = without_session(out["cookie"])
            if not out["cookie"]:
                del out["cookie"]
        # The device's own CSRF checks compare these with its own address.
        for key in ("origin", "referer"):
            if key in out and out[key].startswith(here):
                out[key] = target + out[key][len(here) :]
        out["x-forwarded-for"] = client_address(scope)
        out["x-forwarded-host"] = _host(headers)
        out["x-forwarded-proto"] = here.split(":", 1)[0]
        return out

    async def _http(
        self, scope: Scope, receive: Receive, send: Send, target: str, headers: dict[str, str], here: str
    ) -> None:
        if self._client is None:
            self._client = new_client()

        async def body() -> AsyncIterator[bytes]:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                yield message.get("body", b"")
                if not message.get("more_body"):
                    return

        url = target + _path(scope)
        # A body only when there is one (a GET sent chunked puts some devices off).
        has_body = headers.get("content-length", "0") != "0" or "transfer-encoding" in headers
        request = self._client.build_request(
            scope["method"],
            url,
            headers=self._forwarded(scope, headers, target, here),
            content=body() if has_body else None,
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.HTTPError as e:
            await _deny(scope, send, 502, f"The device doesn't answer: {type(e).__name__}.")
            return
        try:
            out: list[tuple[bytes, bytes]] = []
            for k, v in response.headers.multi_items():
                key = k.lower()
                if key in HOP:
                    continue
                if key == "location" and v.startswith(target):
                    v = here + v[len(target) :]
                if key == "set-cookie":
                    kept = host_only(v, here.startswith("http:"))
                    if kept is None:
                        continue
                    v = kept
                out.append((key.encode(), v.encode()))
            await send({"type": "http.response.start", "status": response.status_code, "headers": out})
            async for chunk in response.aiter_raw():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        finally:
            await response.aclose()

    async def _websocket(
        self, scope: Scope, receive: Receive, send: Send, target: str, headers: dict[str, str], here: str
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

        tasks = [asyncio.ensure_future(to_device()), asyncio.ensure_future(to_browser())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await upstream_ws.close()
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 1000})


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
