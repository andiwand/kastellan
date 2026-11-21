"""Device configuration backups over HTTP (devbackups.py). Seeing which versions there are:
admins. What is in them (a comparison, a file, the download): signed-in admins, audited, since
a backup holds every password of the device (masked when shown, whole in the download)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import devbackups
from .auth_api import client_address, require_admin, require_signin
from .groups_api import SALT_SECRET

if TYPE_CHECKING:
    from .hub import Hub


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    def device_of(id: str) -> Any:
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        return device

    def salt() -> bytes:
        return hub.own_secret(SALT_SECRET).encode()

    def view(id: str) -> dict[str, Any]:
        device = hub.config.devices[id]
        snaps = hub.device_backups.snapshots(id)
        state = {k: v for k, v in (hub.backup_status.get(id) or {}).items() if k != "snapshot"}
        return {
            "device": id,
            "kind": devbackups.kind_of(device),
            "versions": [s.view() for s in reversed(snaps)],
            "status": state,
            "every_hours": hub.config.device_backups.every_hours,
        }

    @api.get("/device-backups")
    async def overview(request: Request) -> list[dict[str, Any]]:
        """Every device that can be backed up (or has versions): newest version, how it went."""
        require_admin(request)
        out = []
        for id, device in hub.config.devices.items():
            snaps = await asyncio.to_thread(hub.device_backups.snapshots, id)
            if not devbackups.kind_of(device) and not snaps:
                continue
            state = hub.backup_status.get(id) or {}
            out.append(
                {
                    "device": id,
                    "kind": devbackups.kind_of(device),
                    "versions": len(snaps),
                    "latest": snaps[-1].view() if snaps else None,
                    "status": {k: v for k, v in state.items() if k != "snapshot"},
                }
            )
        return out

    @api.get("/devices/{id}/backups")
    async def versions(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device_of(id)
        return await asyncio.to_thread(view, id)

    @api.post("/devices/{id}/backups")
    async def now(id: str, request: Request) -> dict[str, Any]:
        """Back it up now."""
        w = require_admin(request)
        device = device_of(id)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is backed up")
        if not devbackups.kind_of(device):
            raise HTTPException(409, "it has no OpenWrt or FRITZ!Box login to take a backup with")
        try:
            result = await devbackups.take(hub, id, by=w.user)
        except Exception as e:
            hub.audit.record(
                "backup.take", w.user, client_address(request.scope), device=id, outcome="failed"
            )
            raise HTTPException(502, (hub.backup_status.get(id) or {}).get("error") or str(e)) from e
        hub.audit.record(
            "backup.take",
            w.user,
            client_address(request.scope),
            device=id,
            outcome="new version" if result["new"] else "unchanged",
        )
        return await asyncio.to_thread(view, id) | {"new": result["new"]}

    @api.get("/devices/{id}/backups/diff")
    async def diff(
        id: str,
        request: Request,
        response: Response,
        older: str = Query(max_length=20),
        newer: str = Query(max_length=20),
    ) -> dict[str, Any]:
        """What changed between two versions; secrets masked."""
        w = require_signin(request)
        response.headers["Cache-Control"] = "no-store"
        device_of(id)
        try:
            out = await asyncio.to_thread(hub.device_backups.diff, id, older, newer, salt())
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        hub.audit.record(
            "backup.compare", w.user, client_address(request.scope), device=id, older=older, newer=newer
        )
        return out

    @api.get("/devices/{id}/backups/{sid}/files")
    async def files(id: str, sid: str, request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        device_of(id)
        try:
            snap = await asyncio.to_thread(hub.device_backups.snapshot, id, sid)
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        return [
            {"path": f.path, "size": f.size, "secret": devbackups.secret_file(f.path)}
            for f in snap.files
            if f.type == "file"
        ]

    @api.get("/devices/{id}/backups/{sid}/file")
    async def file(
        id: str, sid: str, request: Request, response: Response, path: str = Query(max_length=500)
    ) -> dict[str, Any]:
        """One file of a version as text, secrets masked."""
        w = require_signin(request)
        response.headers["Cache-Control"] = "no-store"
        device_of(id)
        try:
            text = await asyncio.to_thread(hub.device_backups.text, id, sid, path, salt())
        except KeyError as e:
            raise HTTPException(404, "no such version or file") from e
        hub.audit.record(
            "backup.read", w.user, client_address(request.scope), device=id, version=sid, path=path
        )
        return {"path": path, "text": text}

    @api.get("/devices/{id}/backups/{sid}/download")
    async def download(id: str, sid: str, request: Request) -> Response:
        """The version as the device takes it back (everything, secrets too)."""
        w = require_signin(request)
        device_of(id)
        try:
            name, data = await asyncio.to_thread(hub.device_backups.archive, id, sid)
        except KeyError as e:
            raise HTTPException(404, "no such version") from e
        hub.audit.record("backup.download", w.user, client_address(request.scope), device=id, version=sid)
        return Response(
            data,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    return api
