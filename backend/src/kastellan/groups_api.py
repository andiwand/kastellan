"""Groups of OpenWrt devices over HTTP (openwrt/sync.py): how their Wi-Fi networks, mesh and
root's password line up, and changing them on every member at once. Seeing: admins; changing:
signed-in admins, streamed (device by device), audited without passwords."""

from __future__ import annotations

import secrets as random
from functools import partial
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import setup_run
from .auth_api import client_address, require_admin, require_signin
from .openwrt import client as ow
from .openwrt import sync

if TYPE_CHECKING:
    from .hub import Hub

SALT_SECRET = "group-fingerprints"  # keys the password fingerprints (never shown)


class NetworkChange(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)  # the network, by its name now
    new_ssid: str | None = Field(default=None, max_length=64)
    key: str | None = Field(default=None, max_length=64)
    encryption: str | None = Field(default=None, max_length=20)


class MeshChange(BaseModel):
    mesh_id: str = Field(max_length=64)
    key: str = Field(max_length=128)  # checked in the handler (a refusal never echoes it)
    radios: dict[str, str] = Field(default_factory=dict, max_length=64)  # device -> radio to add it on
    channel: str | None = Field(default=None, max_length=8)  # one fixed channel for every mesh radio


class RootPassword(BaseModel):
    password: str = Field(max_length=256)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/groups")

    def group_of(gid: str):
        group = hub.config.groups.get(gid)
        if group is None:
            raise HTTPException(404, "no such group")
        return group

    def members(gid: str) -> dict[str, ow.Ubus | Exception]:
        """Each member's API, or why there is none (its login isn't set up yet, say)."""
        out: dict[str, ow.Ubus | Exception] = {}
        for id in group_of(gid).devices:
            try:
                out[id] = hub.ubus(id)
            except KeyError:
                out[id] = LookupError("Kastellan's login isn't set up there yet")
            except Exception as e:
                out[id] = e
        return out

    def reachable(gid: str) -> dict[str, ow.Ubus]:
        """The members a change can go to; one without a login stops it before it starts."""
        all = members(gid)
        broken = {id: str(e) for id, e in all.items() if isinstance(e, Exception)}
        if broken:
            raise HTTPException(409, "; ".join(f"{id}: {why}" for id, why in broken.items()))
        return {id: u for id, u in all.items() if not isinstance(u, Exception)}

    def salt() -> bytes:
        try:
            return hub.secrets.get(SALT_SECRET).encode()
        except KeyError:
            value = random.token_urlsafe(32)
            hub.secrets.set(SALT_SECRET, value)
            return value.encode()

    def check(fn, *args) -> None:
        try:
            fn(*args)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    def signed_in(request: Request):
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is changed")
        return w

    def job(gid: str, action: str, request: Request, work, **logged: Any) -> StreamingResponse:
        w = signed_in(request)
        audit = partial(hub.audit.record, action, w.user, client_address(request.scope), group=gid, **logged)

        async def run(say):
            outcome = await work(say)
            failed = [id for id, o in outcome.items() if o.startswith("failed")]
            if failed:
                raise RuntimeError(f"not on {', '.join(failed)}; the others are done")
            return {"devices": outcome}

        return setup_run.stream_work(run, audit)

    @api.get("")
    async def groups(request: Request) -> list[dict[str, Any]]:
        require_admin(request)
        return [
            {"id": gid, "name": g.name, "devices": g.devices} for gid, g in sorted(hub.config.groups.items())
        ]

    @api.get("/{gid}")
    async def survey(gid: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        group = group_of(gid)
        if hub.demo:
            from .demo import GROUP

            return {"id": gid, "name": group.name, **GROUP}
        found = await sync.survey(members(gid), salt())
        return {"id": gid, "name": group.name, **found}

    @api.post("/{gid}/network")
    async def network(gid: str, body: NetworkChange, request: Request) -> StreamingResponse:
        """A Wi-Fi network (by its name) the same on every member: name, password, encryption."""
        signed_in(request)
        values = {"ssid": body.new_ssid, "key": body.key, "encryption": body.encryption}
        if not any(values.values()):
            raise HTTPException(400, "nothing to change")
        for k, v in values.items():
            if v:
                check(ow.check_value, k, v)
        ubus = reachable(gid)
        return job(
            gid,
            "group.network",
            request,
            lambda say: sync.change_network(ubus, body.ssid, values, say=say),
            ssid=body.ssid,
            new_ssid=body.new_ssid,
            key="***" if body.key else None,
            encryption=body.encryption,
        )

    @api.post("/{gid}/mesh")
    async def mesh(gid: str, body: MeshChange, request: Request) -> StreamingResponse:
        """One mesh across the group, changed on all members at once (sync.set_mesh)."""
        signed_in(request)
        check(ow.check_value, "mesh_id", body.mesh_id)
        check(ow.check_value, "key", body.key)
        if body.channel:
            check(ow.check_value, "channel", body.channel)
        ubus = reachable(gid)
        return job(
            gid,
            "group.mesh",
            request,
            lambda say: sync.set_mesh(
                ubus, body.mesh_id, body.key, body.radios, body.channel or None, say=say
            ),
            mesh_id=body.mesh_id,
            radios=body.radios,
            channel=body.channel,
        )

    @api.post("/{gid}/root-password")
    async def root_password(gid: str, body: RootPassword, request: Request) -> StreamingResponse:
        """root's password, the same on every member (their `password` ability)."""
        signed_in(request)
        if not 8 <= len(body.password) <= 128 or any(ord(c) < 32 for c in body.password):
            raise HTTPException(400, "8 to 128 characters, no control characters")
        ubus = reachable(gid)
        return job(
            gid,
            "group.root_password",
            request,
            lambda say: sync.set_root_password(ubus, body.password, say=say),
        )

    return api
