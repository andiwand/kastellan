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
import json
import logging
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .auth import _DUMMY_HASH, SESSION_COOKIE, SESSION_IDLE, Brake, verify_password
from .config import Role

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from .hub import Hub

log = logging.getLogger(__name__)

PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/me", "/api/auth/logout"}
READS = {"GET", "HEAD"}
CSRF_HEADER = "x-kastellan"


@dataclass(frozen=True)
class Who:
    user: str | None  # None while sign-in is off
    role: Role

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN


def who(request: Request) -> Who:
    return request.scope.get("state", {}).get("who") or Who(None, Role.ADMIN)


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


def client_address(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "?"


class Guard:
    """ASGI middleware: who is asking, and may they."""

    def __init__(self, app: ASGIApp, hub: Hub) -> None:
        self.app, self.hub = app, hub

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        method = scope.get("method", "GET")
        users = self.hub.config.users

        if scope["type"] == "http" and method not in READS and headers.get(CSRF_HEADER) != "1":
            await _deny(scope, send, 403, "missing the X-Kastellan header")
            return

        if not users:
            w = Who(None, Role.ADMIN)
        else:
            cookie = SimpleCookie()
            cookie.load(headers.get("cookie", ""))
            token = cookie[SESSION_COOKIE].value if SESSION_COOKIE in cookie else None
            user = self.hub.sessions.user(token)
            if user is not None and user not in users:  # removed from config.yaml
                self.hub.sessions.end(token)
                user = None
            if user is None:
                if scope["path"] in PUBLIC:
                    w = None
                else:
                    await _deny(scope, send, 401, "sign in first")
                    return
            else:
                w = Who(user, users[user].role)
                writes = scope["type"] == "http" and method not in READS
                if writes and not w.is_admin and scope["path"] not in PUBLIC:
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
    user: str
    password: str


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

    @api.post("/auth/login")
    async def login(body: SignIn, request: Request, response: Response) -> dict[str, Any]:
        address = client_address(request.scope)
        if not hub.config.users:
            raise HTTPException(400, "sign-in is off: there are no users in config.yaml")
        if wait := brake.blocked(address):
            hub.audit.record("signin.blocked", body.user, address)
            raise HTTPException(429, f"too many attempts; try again in {int(wait) + 1} s")
        user = hub.config.users.get(body.user)
        # The hash takes ~50 ms; compare against a dummy for unknown names so timing tells nothing.
        ok = await asyncio.to_thread(verify_password, body.password, user.password if user else _DUMMY_HASH)
        if not (ok and user):
            brake.failed(address)
            hub.audit.record("signin.failed", body.user, address)
            raise HTTPException(401, "wrong user or password")
        brake.succeeded(address)
        token = hub.sessions.create(body.user)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_IDLE,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        hub.audit.record("signin", body.user, address)
        return {"user": body.user, "role": user.role}

    @api.post("/auth/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        w = request.scope.get("state", {}).get("who")
        hub.sessions.end(request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        if w and w.user:
            hub.audit.record("signout", w.user, client_address(request.scope))
        return {"ok": True}

    @api.get("/audit")
    def audit(request: Request, limit: int = 200) -> list[dict[str, Any]]:
        require_admin(request)
        return hub.audit.recent(min(max(limit, 1), 2000))

    return api
