"""SSH keys over HTTP (sshkeys.py): the keys on a device, removing one, and rotating the hub key.

Seeing keys: admins. Changing them (removing one, rotating): signed-in admins, audited.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import sshkeys
from .auth_api import client_address, require_admin, require_signin
from .ssh import ChangedHost, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


class Remove(BaseModel):
    user: str = Field(min_length=1, max_length=64)
    file: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=10, max_length=100)


class Finish(BaseModel):
    anyway: bool = False  # switch although the new key isn't on every device


def _brief(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:120]}"


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")
    rotator = sshkeys.Rotator(hub.keys)

    def labels() -> dict[str, str]:
        return sshkeys.kastellan_keys(hub.keys)

    def device_of(id: str):
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        if device.ssh is None:
            raise HTTPException(409, "the device has no ssh: login in config.yaml")
        return device

    def failed(e: Exception) -> HTTPException:
        if isinstance(e, UnknownHost | ChangedHost):
            return HTTPException(409, f"{e} (trust the host key first)")
        if isinstance(e, asyncssh.PermissionDenied):
            return HTTPException(502, "Kastellan's key isn't accepted there (set up access first)")
        if isinstance(e, PermissionError):
            return HTTPException(403, str(e))
        if isinstance(e, TimeoutError | asyncssh.TimeoutError):
            return HTTPException(504, "timed out")
        return HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")

    def rotation_view() -> dict[str, Any] | None:
        rot = rotator.state()
        if rot is None:
            return None
        current = {id: sshkeys.login_of(d) for id, d in hub.config.devices.items() if d.ssh}
        steps = {}
        for id, s in rot.devices.items():
            # As finish() sees it: done for the login the device has now (it may have changed).
            steps[id] = vars(s) | {"current": current.get(id) == s.target}
        return {
            "started": rot.started,
            "stage": rot.stage,
            "finished": rot.finished,
            "devices": steps,
            # Devices the new key isn't on (for their login now): Switch needs them, or "anyway".
            "missing": sorted(
                id
                for id, login in current.items()
                if not (id in rot.devices and rot.devices[id].added and rot.devices[id].target == login)
            ),
        }

    @api.get("/ssh/keys")
    def hub_keys(request: Request) -> dict[str, Any]:
        require_admin(request)
        key = hub.keys.hub_key()
        made = datetime.fromtimestamp(os.stat(hub.keys.private_path).st_mtime, UTC)
        nxt = hub.keys.side_key("next")
        return {
            "public_key": hub.keys.public_key(),
            "fingerprint": key.get_fingerprint(),
            "made": made.isoformat(timespec="seconds"),
            "next": nxt.get_fingerprint() if nxt else None,
            "rotation": rotation_view(),
            "devices": sorted(id for id, d in hub.config.devices.items() if d.ssh),
        }

    @api.get("/devices/{id}/keys")
    async def device_keys(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device = device_of(id)
        try:
            found, everyone = await sshkeys.list_keys(hub.keys, device)
        except Exception as e:
            raise failed(e) from e
        names = labels()
        return {"everyone": everyone, "login": device.ssh.user, "keys": [k.view(names) for k in found]}

    @api.post("/devices/{id}/keys/remove")
    async def remove_key(id: str, body: Remove, request: Request) -> dict[str, Any]:
        w = require_signin(request)
        device = device_of(id)
        if body.fingerprint in labels():
            raise HTTPException(
                400, "that is one of Kastellan's own keys; rotate it on the Access page instead"
            )
        details = {"device": id, "account": body.user, "fingerprint": body.fingerprint}
        audit = partial(hub.audit.record, "ssh.key.remove", w.user, client_address(request.scope), **details)
        try:
            async with sshkeys.device_lock(id):
                removed = await sshkeys.remove_key(hub.keys, device, body.user, body.file, body.fingerprint)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:
            audit(outcome=f"failed: {_brief(e)}")
            raise failed(e) from e
        audit(outcome="done")
        return {"removed": removed}

    def devices() -> dict[str, Any]:
        return {id: d for id, d in hub.config.devices.items() if d.ssh}

    def audit_rotation(request: Request, user: str | None, what: str, rot: sshkeys.Rotation | None) -> None:
        steps = {
            id: "removed the old key" if s.removed else "has the new key" if s.added else s.error
            for id, s in (rot.devices.items() if rot else [])
        }
        hub.audit.record(
            f"ssh.rotate.{what}",
            user,
            client_address(request.scope),
            stage=rot.stage if rot else None,
            devices=steps,
        )

    @api.post("/ssh/rotate/start")
    async def rotate_start(request: Request) -> dict[str, Any]:
        w = require_signin(request)
        try:
            rot = await rotator.start(devices())
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        audit_rotation(request, w.user, "start", rot)
        return {"rotation": rotation_view()}

    @api.post("/ssh/rotate/finish")
    async def rotate_finish(body: Finish, request: Request) -> dict[str, Any]:
        w = require_signin(request)
        try:
            rot = await rotator.finish(devices(), anyway=body.anyway)
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(409, str(e)) from e
        audit_rotation(request, w.user, "finish", rot)
        return {"rotation": rotation_view()}

    @api.post("/ssh/rotate/cancel")
    async def rotate_cancel(request: Request) -> dict[str, Any]:
        w = require_signin(request)
        try:
            await rotator.cancel()
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        audit_rotation(request, w.user, "cancel", None)
        return {"rotation": None}

    return api
