"""Sign-in over HTTP: the guard in front of /api, and the sign-in, sign-out and audit routes.

The guard is plain ASGI middleware so it covers WebSockets too (the terminal and remote desktop
use them). It decides, for every /api request:

- Sign-in off (no users in config.yaml): everyone may do everything, as `None` with the admin
  role. Routes that hand out shells or screens check `require_signin` and refuse.
- Sign-in on: a live session cookie is needed, except for PUBLIC paths. Observers may only read
  (GET/HEAD); anything else needs an admin.
- Writes (not GET/HEAD) need the header `X-Kastellan: 1`. A page on another site can't set it
  without the browser asking first, which this server never allows, so a forged form or link
  can't act in a signed-in user's name (on top of the SameSite=Strict cookie).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth import DUMMY_HASH, SESSION_COOKIE, SESSION_IDLE, Brake, password_tag, verify_password
from .config import Role
from .terminals import token_key

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from .hub import Hub

log = logging.getLogger(__name__)

PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/me", "/api/auth/logout"}
# A backup job reporting in (backups_api): its own token instead of a session; no cookie, so no
# CSRF header either.
REPORT_PATH = re.compile(r"/api/backups/[a-z0-9_-]{1,64}/(ok|fail)")
READS = {"GET", "HEAD"}
# What a signed-in observer may change: only their own browsers' push subscriptions (push_api).
OWN = {"/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test"}
CSRF_HEADER = "x-kastellan"


@dataclass(frozen=True)
class Who:
    user: str | None  # None while sign-in is off
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


# What a route sees if the guard did not run (a route outside /api): no rights at all.
NOBODY = Who(None, Role.OBSERVER)


def who(request: Request) -> Who:
    return request.scope.get("state", {}).get("who") or NOBODY


def require_admin(request: Request) -> Who:
    w = who(request)
    if not w.is_admin:
        raise HTTPException(403, "admins only")
    return w


def require_signin(request: Request) -> Who:
    """For what hands out access to devices (terminal, remote desktop): an admin, signed in.
    Never available while sign-in is off, however the hub is reached."""
    w = require_admin(request)
    if w.user is None:
        raise HTTPException(403, "turn on sign-in first (add a user to config.yaml)")
    return w


# The longest password typed anywhere (sign-in, typed again to confirm).
MAX_PASSWORD = 1024


async def confirm_password(hub: Hub, w: Who, password: str) -> None:
    """The asking admin's own password, typed again before a secret is shown or used: 403 if
    it isn't theirs."""
    user = hub.config.users.get(w.user or "")
    if user is None or not await asyncio.to_thread(verify_password, password, user.password):
        raise HTTPException(403, "wrong password")


LOCAL_NAMES = {"localhost"}


def allowed_host(host: str, names: tuple[str, ...]) -> bool:
    """IP addresses (which can't be rebound), localhost, and the configured names."""
    name = host.lower()
    if name.startswith("["):  # [::1]:8100
        name = name[1 : name.find("]")]
    elif name.count(":") == 1:
        name = name.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name in LOCAL_NAMES or name in names


def same_origin(headers: dict[str, str]) -> bool:
    """Whether a browser request comes from this hub's own page: Origin names the same host."""
    origin, host = headers.get("origin"), headers.get("host")
    return bool(origin and host) and urlsplit(origin).netloc == host


def client_address(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "?"


def session_token(headers: dict[str, str]) -> str | None:
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except Exception:  # a malformed Cookie header is no session
        return None
    return cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else None


def session_who(hub: Hub, token: str | None) -> Who | None:
    """Who a session belongs to now, or None: also when the user was removed from config.yaml or
    their password changed since (then the session ends). Used at every request, and again and
    again while a terminal is open."""
    users = hub.config.users
    user = hub.sessions.user(token)
    if user is None:
        return None
    if user not in users or hub.sessions.tag(token) != password_tag(users[user].password):
        hub.sessions.end(token)
        return None
    return Who(user, users[user].role)


# Sent with every answer of the hub itself (not the device pages behind it): no framing, its own
# browsing context group and agent cluster (a device page on a sibling name can't reach into it).
HUB_HEADERS = [
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"origin-agent-cluster", b"?1"),
    (b"referrer-policy", b"same-origin"),
    (b"x-content-type-options", b"nosniff"),
]


class HubHeaders:
    """ASGI middleware: HUB_HEADERS on the hub's own answers, where the answer has none of its own."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def with_headers(message) -> None:
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [
                    *message.get("headers", []),
                    *(h for h in HUB_HEADERS if h[0] not in have),
                ]
            await send(message)

        await self.app(scope, receive, with_headers)


class Guard:
    """ASGI middleware: who is asking, and may they. A backup job's report (REPORT_PATH) passes
    without a session and without the CSRF header: backups_api checks its own token."""

    def __init__(self, app: ASGIApp, hub: Hub) -> None:
        self.app, self.hub = app, hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        method = scope.get("method", "GET")
        users = self.hub.config.users

        web = self.hub.config.web
        names = (*self.hub.settings.hosts, web.domain) if web else self.hub.settings.hosts
        if not allowed_host(headers.get("host", ""), names):
            # DNS rebinding: a page on an attacker's name that now points here. Its requests carry
            # that name, which the hub doesn't answer to.
            await _deny(scope, send, 421, "this hub doesn't answer to that name (see KASTELLAN_HOSTS)")
            return
        if self.hub.locked and scope["path"] != "/api/health":
            # config.yaml was broken at start: refuse everything rather than run without sign-in.
            await _deny(scope, send, 503, "locked until config.yaml is fixed (see /api/health)")
            return
        if scope["type"] == "http" and headers.get("sec-fetch-site") in ("same-site", "cross-site"):
            # Another page asked (a device's page on a sibling name shares the site): the hub's
            # API answers only its own page. Its cookie is SameSite=Strict, which a sibling's
            # requests still carry; this is what keeps them out.
            await _deny(scope, send, 403, "only the hub's own page may ask this")
            return
        if scope["type"] == "websocket" and not same_origin(headers):
            # A page on another site (or a sibling host on the same site) must not open a
            # WebSocket with the user's cookie.
            await _deny(scope, send, 403, "WebSockets only from this hub's own page")
            return

        if scope["type"] == "http" and method == "POST" and REPORT_PATH.fullmatch(scope["path"]):
            scope.setdefault("state", {})["who"] = None
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and method not in READS and headers.get(CSRF_HEADER) != "1":
            await _deny(scope, send, 403, "missing the X-Kastellan header")
            return

        if not users:
            w = Who(None, Role.ADMIN)
        else:
            w = session_who(self.hub, session_token(headers))
            if w is None:
                if scope["path"] not in PUBLIC:
                    await _deny(scope, send, 401, "sign in first")
                    return
            else:
                # A page refreshing on its own (X-Kastellan-Auto) isn't someone using the app.
                if headers.get("x-kastellan-auto") != "1":
                    self.hub.sessions.mark(w.user)
                writes = scope["type"] == "http" and method not in READS
                if writes and not w.is_admin and scope["path"] not in PUBLIC | OWN:
                    await _deny(scope, send, 403, "observers can't change anything")
                    return
        scope.setdefault("state", {})["who"] = w
        await self.app(scope, receive, send)


async def _deny(scope: Scope, send: Send, status: int, message: str) -> None:
    if scope["type"] == "websocket":
        # Closing before accepting makes the server answer the handshake with 403.
        await send({"type": "websocket.close", "code": 4401 if status == 401 else 4403, "reason": message})
        return
    body = json.dumps({"detail": message}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SignIn(BaseModel):
    user: str = Field(max_length=64)
    password: str = Field(max_length=MAX_PASSWORD)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")
    brake = Brake()

    @api.get("/auth/me")
    def me(request: Request) -> dict[str, Any]:
        w = request.scope.get("state", {}).get("who")
        return {
            "signin": bool(hub.config.users),
            "user": w.user if w else None,
            "role": w.role if w else None,
        }

    # Each check costs ~32 MB and ~50 ms; anyone may ask, so only a few run at a time. Parallel
    # guesses can't slip past the brake: it is asked and counted with no await in between.
    hashing = asyncio.Semaphore(4)

    @api.post("/auth/login")
    async def login(body: SignIn, request: Request, response: Response) -> dict[str, Any]:
        address = client_address(request.scope)
        if not hub.config.users:
            raise HTTPException(400, "sign-in is off: there are no users in config.yaml")
        if wait := brake.blocked(address):
            if brake.first_block(address):
                hub.audit.record("signin.blocked", body.user, address)
            raise HTTPException(429, f"too many attempts; try again in {int(wait) + 1} s")
        # Counted before checking, so the attempt counts however it ends.
        counted = brake.failed(address)
        user = hub.config.users.get(body.user)
        async with hashing:
            # A dummy hash for unknown names, so the time taken tells nothing.
            ok = await asyncio.to_thread(
                verify_password, body.password, user.password if user else DUMMY_HASH
            )
        if not (ok and user):
            hub.audit.record("signin.failed", body.user, address)
            raise HTTPException(401, "wrong user or password")
        brake.succeeded(address, counted)
        # The session first: rings that stay open while an admin is here see them at once.
        token = hub.sessions.create(body.user, password_tag(user.password))
        # The password just typed opens the key ring (an admin's): it is never kept.
        if opened := await hub.unlock_on_signin(body.user, body.password):
            hub.audit.record("vault.unlock", body.user, address, by="sign-in", rings=opened)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_IDLE,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",  # host-only: device names get their own access (webproxy.py)
        )
        hub.audit.record("signin", body.user, address)
        return {"user": body.user, "role": user.role}

    @api.post("/auth/logout")
    async def logout(request: Request, response: Response, everywhere: bool = False) -> dict[str, bool]:
        w = who(request)
        token = request.cookies.get(SESSION_COOKIE)
        if everywhere and w and w.user:
            hub.sessions.end_user(w.user)
            hub.terminals.stop(user=w.user)
            hub.tickets.drop(user=w.user)
        hub.terminals.stop(token=token)  # its open terminals and desktops end with it
        hub.tickets.drop(session=token_key(token))
        hub.sessions.end(token)
        response.delete_cookie(SESSION_COOKIE, path="/")
        if w and w.user:
            hub.audit.record("signout", w.user, client_address(request.scope))
        return {"ok": True}

    @api.get("/audit")
    def audit(request: Request, limit: int = 200) -> list[dict[str, Any]]:
        require_admin(request)
        return hub.audit.recent(min(max(limit, 1), 2000))

    return api
