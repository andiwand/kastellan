"""Kastellan's own backup over HTTP (hubbackup.py): its state and versions (admins); the
passphrase made once and shown once, a backup now, what changed, the download (signed-in
admins, audited). Also checks that you still have the passphrase and the key rings' recovery
keys: a backup nobody can open saves nothing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from . import devbackups, hubbackup
from .auth import Brake
from .auth_api import client_address, require_admin, require_signin
from .groups_api import SALT_SECRET

if TYPE_CHECKING:
    from .hub import Hub


class Passphrase(BaseModel):
    replace: bool = False  # a new one: the old store is set aside, its passphrase still opens it
    # keyring: the backup opens with the key rings (an admin's password or a recovery key), no
    # passphrase to keep; passphrase: one made now and shown once.
    mode: Literal["keyring", "passphrase"] = "passphrase"


class Check(BaseModel):
    passphrase: str | None = Field(default=None, max_length=200)
    ring: str | None = Field(default=None, max_length=40)  # with recovery: a key ring's recovery key
    recovery: str | None = Field(default=None, max_length=200)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/hub-backup")
    brake = Brake(limit=10)
    checks_file = hub.hub_backup.folder.parent / "hub-backup-checks.json"
    checking = asyncio.Semaphore(2)

    def checks() -> dict[str, Any]:
        try:
            return json.loads(checks_file.read_text())
        except (OSError, ValueError):
            return {}

    def checked(what: str, key: str) -> None:
        data = checks() | {what: {"at": datetime.now(UTC).isoformat(timespec="seconds"), "key": key}}
        hubbackup.private_write(checks_file, json.dumps(data).encode())

    def recovery_version(ring: str) -> str | None:
        if ring == "kastellan" and (hub.hub_backup.header() or {}).get("keyring"):
            path = hub.hub_backup.folder / hubbackup.KEYRING
            return hashlib.sha256(hubbackup.read_bounded(path)).hexdigest() if path.exists() else None
        return hub.vault.recovery_tag(ring)

    async def background(work):
        task = asyncio.create_task(work)
        hub._background.add(task)
        task.add_done_callback(hub._background.discard)
        # Retrieve errors even if the browser leaves while the operation finishes.
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return await asyncio.shield(task)

    def local_store(header):
        # Legacy folders can still be downloaded as an authenticated .kbak before rotation.
        return (
            devbackups.Store(hub.hub_backup.folder, hub._hub_backup_key)
            if header and header.get("version") == 1
            else hub.hub_backup.store
        )

    @api.get("")
    async def view(request: Request, response: Response) -> dict[str, Any]:
        require_admin(request)
        hb = hub.hub_backup
        response.headers["Cache-Control"] = "no-store"
        async with hb.operation:
            header = hb.header()
            snaps = await hubbackup.blocking(local_store(header).snapshots, hubbackup.ID) if header else []
        cfg = hub.config.hub_backup
        versions = {"passphrase": (header or {}).get("check")}
        if not hub.demo and hub.vault.exists:
            versions |= {f"recovery:{r['id']}": recovery_version(r["id"]) for r in hub.vault.rings()}
        verified = {
            what: item["at"]
            for what, item in checks().items()
            if isinstance(item, dict) and item.get("key") == versions.get(what) and item.get("at")
        }
        return {
            "ready": header is not None,
            "legacy": bool(header and header.get("version") == 1),
            "keyring": bool(header and header.get("keyring")),
            "vault": hub.vault.exists and not hub.demo,
            "made": (header or {}).get("made"),
            "status": hb.status,
            "versions": [s.view() for s in reversed(snaps)],
            "every_hours": cfg.every_hours,
            "keep": cfg.keep,
            "targets": [t.model_dump() for t in cfg.targets],
            "checks": verified,
            "rings": [r["id"] for r in hub.vault.rings()] if not hub.demo and hub.vault.exists else [],
        }

    @api.post("/passphrase")
    async def passphrase(body: Passphrase, request: Request, response: Response) -> dict[str, Any]:
        """The passphrase, made now and shown once. Write it down: restoring needs it."""
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is kept")

        async def make():
            async with hub.hub_backup.operation:
                if hub.hub_backup.header() is not None and not body.replace:
                    raise HTTPException(409, "there is one already; a new one starts a new backup")
                if body.mode == "keyring":
                    from .vault import KASTELLAN

                    if not (hub.vault.exists and hub.vault.is_open(KASTELLAN)):
                        raise HTTPException(
                            409, "the backup opens with the key rings: make them (Keys) or open them"
                        )
                    phrase = None
                    made = hubbackup.make_keyring_key()
                else:
                    phrase = hubbackup.new_passphrase()
                    made = await hubbackup.blocking(hubbackup.make_key, phrase)
                # Fail before moving the store when its existing secret cannot be read (locked ring).
                if hub.secrets.has(hubbackup.KEY_SECRET):
                    hub.secrets.get(hubbackup.KEY_SECRET)

                def commit():
                    # One uncancellable file operation: cancellation must not split store and key rotation.
                    aside = hub.hub_backup.start(made)
                    try:
                        hub.secrets.set(
                            hubbackup.KEY_SECRET,
                            json.dumps({"salt": made.get("salt", ""), "key": made["key"]}),
                        )
                    except BaseException:
                        import shutil

                        shutil.rmtree(hub.hub_backup.folder)
                        if aside:
                            aside.rename(hub.hub_backup.folder)
                        raise
                    hub.hub_backup.status = {}
                    old_checks = checks()
                    old_checks.pop("passphrase", None)
                    hubbackup.private_write(checks_file, json.dumps(old_checks).encode())
                    hub.audit.record(
                        "hubbackup.passphrase",
                        w.user,
                        client_address(request.scope),
                        replaced=body.replace,
                        mode=body.mode,
                    )

                await hubbackup.blocking(commit)
                return {"passphrase": phrase}

        response.headers["Cache-Control"] = "no-store"
        return await background(make())

    @api.post("/check")
    async def check(body: Check, request: Request) -> dict[str, Any]:
        """Whether a passphrase (or a ring's recovery key) is right; nothing else happens."""
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode has no recovery keys to check")
        address = client_address(request.scope) or "?"
        if brake.blocked(address):
            raise HTTPException(429, "too many wrong tries; try again later")
        if body.passphrase:
            header = hub.hub_backup.header()
            if header is None:
                raise HTTPException(409, "no backup passphrase yet")
            try:
                async with checking, hub.hub_backup.operation:
                    await hubbackup.blocking(hubbackup.key_from, header, body.passphrase)
                    if header != hub.hub_backup.header():
                        raise HTTPException(409, "the backup passphrase changed; try again")
                ok = True
            except ValueError:
                ok = False
            what, key = "passphrase", header["check"]
        elif body.ring and body.recovery:
            async with checking, hub.hub_backup.operation:
                key = recovery_version(body.ring)
                if key is None:
                    raise HTTPException(409, "make a backup before checking its recovery key")
                header = hub.hub_backup.header()
                if body.ring == "kastellan" and header and header.get("keyring"):
                    try:
                        await hubbackup.blocking(
                            hubbackup.key_from_keyring,
                            header,
                            hubbackup.read_bounded(hub.hub_backup.folder / hubbackup.KEYRING),
                            None,
                            None,
                            body.recovery,
                        )
                        ok = True
                    except ValueError:
                        ok = False
                else:
                    ok = await hubbackup.blocking(hub.vault.check_recovery, body.ring, body.recovery)
                if key != recovery_version(body.ring):
                    raise HTTPException(409, "the recovery key changed; try again")
            what = f"recovery:{body.ring}"
        else:
            raise HTTPException(400, "a passphrase, or a ring and its recovery key")
        if not ok:
            brake.failed(address)
        else:
            checked(what, key)
        hub.audit.record("hubbackup.check", w.user, address, what=what, right=ok)
        return {"right": ok}

    @api.post("")
    async def now(request: Request) -> dict[str, Any]:
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is backed up")
        if hub.hub_backup.operation.locked():
            raise HTTPException(409, "a backup operation is already running")

        async def take():
            hub.audit.record("hubbackup.take", w.user, client_address(request.scope), outcome="started")
            try:
                status = await hubbackup.run_once(hub, by=w.user)
            except BaseException as e:
                hub.audit.record("hubbackup.take", w.user, client_address(request.scope), outcome="failed")
                if not isinstance(e, Exception):
                    raise
                raise HTTPException(502, str(e)[:300] or type(e).__name__) from e
            hub.audit.record(
                "hubbackup.take", w.user, client_address(request.scope), outcome=status.get("error") or "done"
            )
            return status

        return await background(take())

    @api.get("/diff")
    async def diff(
        request: Request,
        response: Response,
        older: str = Query(max_length=20),
        newer: str = Query(max_length=20),
    ):
        w = require_signin(request)
        try:
            async with hub.hub_backup.operation:
                out = await hubbackup.blocking(
                    local_store(hub.hub_backup.header()).diff,
                    hubbackup.ID,
                    older,
                    newer,
                    hub.own_secret(SALT_SECRET).encode(),
                )
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        hub.audit.record("hubbackup.compare", w.user, client_address(request.scope), older=older, newer=newer)
        response.headers["Cache-Control"] = "no-store"
        return out

    @api.get("/{sid}/download")
    async def download(sid: str, request: Request) -> Response:
        """One version as a single encrypted file (`kastellan restore <file>` takes it)."""
        w = require_signin(request)
        async with hub.hub_backup.operation:
            header = hub.hub_backup.header()
            if header is None:
                raise HTTPException(404, "no backup yet")
            try:
                data = await hubbackup.blocking(
                    hubbackup.encrypted_archive, local_store(header), header, sid, hub._hub_backup_key()
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
