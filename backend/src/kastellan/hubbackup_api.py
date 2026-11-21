"""Kastellan's own backup over HTTP (hubbackup.py): its state and versions (admins); the
passphrase made once and shown once, a backup now, what changed, the download (signed-in
admins, audited). Also checks that you still have the passphrase and the key rings' recovery
keys: a backup nobody can open saves nothing."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import hubbackup
from .auth import Brake
from .auth_api import client_address, require_admin, require_signin
from .groups_api import SALT_SECRET

if TYPE_CHECKING:
    from .hub import Hub


class Passphrase(BaseModel):
    replace: bool = False  # a new one: the old store is set aside, its passphrase still opens it


class Check(BaseModel):
    passphrase: str | None = Field(default=None, max_length=200)
    ring: str | None = Field(default=None, max_length=40)  # with recovery: a key ring's recovery key
    recovery: str | None = Field(default=None, max_length=200)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/hub-backup")
    brake = Brake(limit=10)
    checks_file = hub.settings.data / "hub-backup-checks.json"

    def checks() -> dict[str, str]:
        try:
            return json.loads(checks_file.read_text())
        except (OSError, ValueError):
            return {}

    def checked(what: str) -> None:
        data = checks() | {what: datetime.now(UTC).isoformat(timespec="seconds")}
        checks_file.write_text(json.dumps(data))

    @api.get("")
    async def view(request: Request) -> dict[str, Any]:
        require_admin(request)
        hb = hub.hub_backup
        header = hb.header()
        snaps = await asyncio.to_thread(hb.store.snapshots, hubbackup.ID) if header else []
        cfg = hub.config.hub_backup
        return {
            "ready": header is not None,
            "made": (header or {}).get("made"),
            "status": hb.status,
            "versions": [s.view() for s in reversed(snaps)],
            "every_hours": cfg.every_hours,
            "keep": cfg.keep,
            "targets": [t.model_dump() for t in cfg.targets],
            "checks": checks(),
            "rings": [r for r in ("kastellan", "people") if hub.vault.exists],
        }

    @api.post("/passphrase")
    async def passphrase(body: Passphrase, request: Request, response: Response) -> dict[str, Any]:
        """The passphrase, made now and shown once. Write it down: restoring needs it."""
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is kept")
        if hub.hub_backup.header() is not None and not body.replace:
            raise HTTPException(409, "there is one already; a new one starts a new backup")
        phrase = hubbackup.new_passphrase()
        made = await asyncio.to_thread(hubbackup.make_key, phrase)
        hub.secrets.set(hubbackup.KEY_SECRET, json.dumps({"salt": made["salt"], "key": made["key"]}))
        await asyncio.to_thread(hub.hub_backup.start, made)
        hub.hub_backup.status = {}
        response.headers["Cache-Control"] = "no-store"
        hub.audit.record("hubbackup.passphrase", w.user, client_address(request.scope), replaced=body.replace)
        return {"passphrase": phrase}

    @api.post("/check")
    async def check(body: Check, request: Request) -> dict[str, Any]:
        """Whether a passphrase (or a ring's recovery key) is right; nothing else happens."""
        w = require_signin(request)
        address = client_address(request.scope) or "?"
        if brake.blocked(address):
            raise HTTPException(429, "too many wrong tries; try again later")
        if body.passphrase:
            header = hub.hub_backup.header()
            if header is None:
                raise HTTPException(409, "no backup passphrase yet")
            try:
                await asyncio.to_thread(hubbackup.key_from, header, body.passphrase)
                ok = True
            except ValueError:
                ok = False
            what = "passphrase"
        elif body.ring and body.recovery:
            ok = await asyncio.to_thread(hub.vault.check_recovery, body.ring, body.recovery)
            what = f"recovery:{body.ring}"
        else:
            raise HTTPException(400, "a passphrase, or a ring and its recovery key")
        if not ok:
            brake.failed(address)
        else:
            checked(what)
        hub.audit.record("hubbackup.check", w.user, address, what=what, right=ok)
        return {"right": ok}

    @api.post("")
    async def now(request: Request) -> dict[str, Any]:
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is backed up")
        try:
            status = await hubbackup.run_once(hub, by=w.user)
        except Exception as e:
            hub.audit.record("hubbackup.take", w.user, client_address(request.scope), outcome="failed")
            raise HTTPException(502, str(e)[:300] or type(e).__name__) from e
        hub.audit.record(
            "hubbackup.take", w.user, client_address(request.scope), outcome=status.get("error") or "done"
        )
        return status

    @api.get("/diff")
    async def diff(request: Request, older: str = Query(max_length=20), newer: str = Query(max_length=20)):
        w = require_signin(request)
        try:
            out = await asyncio.to_thread(
                hub.hub_backup.store.diff, hubbackup.ID, older, newer, hub.own_secret(SALT_SECRET).encode()
            )
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        hub.audit.record("hubbackup.compare", w.user, client_address(request.scope), older=older, newer=newer)
        return out

    @api.get("/{sid}/download")
    async def download(sid: str, request: Request) -> Response:
        """One version as a single encrypted file (`kastellan restore <file>` takes it)."""
        w = require_signin(request)
        header = hub.hub_backup.header()
        if header is None:
            raise HTTPException(404, "no backup yet")
        try:
            data = await asyncio.to_thread(
                hubbackup.encrypted_archive, hub.hub_backup.store, header, sid, hub._hub_backup_key()
            )
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        hub.audit.record("hubbackup.download", w.user, client_address(request.scope), version=sid)
        stamp = time.strftime("%Y-%m-%d")
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="kastellan-{stamp}.kbak"',
                "Cache-Control": "no-store",
            },
        )

    return api
