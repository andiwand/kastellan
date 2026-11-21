"""Backups over HTTP: their state (signed in), a new token for one (admins, audited), and the
backup jobs' own calls (`ok`, `fail`), which carry that token instead of a session (the Guard
lets these two through; see auth_api.REPORT_PATH)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response

from .auth import Brake
from .auth_api import client_address, require_signin

if TYPE_CHECKING:
    from .hub import Hub


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/backups")
    brake = Brake(limit=10)

    @api.get("")
    async def backups() -> list[dict[str, Any]]:
        return hub.backups.view(hub.config, datetime.now(UTC))

    @api.post("/{id}/token")
    async def token(id: str, request: Request, response: Response) -> dict[str, str]:
        w = require_signin(request)
        if id not in hub.config.backups:
            raise HTTPException(404, "no such backup in config.yaml")
        try:
            value = hub.backups.new_token(id, datetime.now(UTC))
        except OSError as e:
            raise HTTPException(503, "could not save the backup token") from e
        response.headers["Cache-Control"] = "no-store"
        hub.audit.record("backup.token", w.user, client_address(request.scope), backup=id)
        return {"token": value}

    async def report(id: str, request: Request, ok: bool) -> dict[str, bool]:
        address = client_address(request.scope) or "?"
        if brake.blocked(address):
            raise HTTPException(429, "too many wrong tokens; try again later")
        auth = request.headers.get("authorization", "")
        given = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else ""
        if id not in hub.config.backups or not hub.backups.valid(id, given):
            brake.failed(address)
            raise HTTPException(401, "no such backup, or not its token")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 2000:
                raise HTTPException(413, "backup reports are limited to 2000 bytes")
            body.extend(chunk)
        # Config or token rotation may have changed while the body arrived.
        if id not in hub.config.backups or not hub.backups.valid(id, given):
            raise HTTPException(401, "no such backup, or not its token")
        try:
            hub.backups.report(id, ok, body.decode(errors="replace") if not ok else "", datetime.now(UTC))
        except OSError as e:
            raise HTTPException(503, "could not save the backup report") from e
        return {"ok": True}

    @api.post("/{id}/ok")
    async def ok(id: str, request: Request) -> dict[str, bool]:
        return await report(id, request, True)

    @api.post("/{id}/fail")
    async def fail(id: str, request: Request) -> dict[str, bool]:
        return await report(id, request, False)

    return api
