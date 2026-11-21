"""Groups over HTTP: for a group's OpenWrt members (openwrt/sync.py), how their Wi-Fi networks,
mesh and root's password line up, and changing them on every one at once; for its members with
`ssh:`, their authorized_keys and known_hosts side by side, changed on all (sshsync.py). (The
groups themselves are edited as a part of config.yaml: config_api's settings.) Seeing: admins; changing:
signed-in admins, streamed (device by device), audited without passwords."""

from __future__ import annotations

import asyncio
import secrets as random
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import roaming, setup_run, sshkeys, sshsync
from .auth_api import client_address, require_admin, require_signin
from .config import Device
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


class ColumnChange(BaseModel):
    version: str = Field(max_length=32)  # the file as fetched ("none": there was none)
    add: list[str] = Field(default_factory=list, max_length=200)
    remove: list[str] = Field(default_factory=list, max_length=200)  # entries' keys


class SshPush(BaseModel):
    file: sshsync.File
    changes: dict[str, ColumnChange] = Field(max_length=500)  # "device/user" -> what to change


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
            device = hub.config.devices.get(id)
            if device is None or device.openwrt is None:
                continue  # only OpenWrt members have these settings
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

    def check(fn, *args):
        try:
            return fn(*args)
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
            {
                "id": gid,
                "name": g.name,
                "devices": g.devices,
                "openwrt": [id for id in g.devices if (d := hub.config.devices.get(id)) and d.openwrt],
                "ssh": [id for id in g.devices if (d := hub.config.devices.get(id)) and d.ssh],
            }
            for gid, g in sorted(hub.config.groups.items())
        ]

    @api.get("/{gid}")
    async def survey(gid: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        group = group_of(gid)
        if not any((d := hub.config.devices.get(id)) and d.openwrt for id in group.devices):
            raise HTTPException(404, "no OpenWrt devices in this group")
        if hub.demo:
            from .demo import GROUP

            found = GROUP
        else:
            found = await sync.survey(members(gid), salt())
        ids = [id for id in group.devices if (d := hub.config.devices.get(id)) and d.openwrt]
        return {
            "id": gid,
            "name": group.name,
            **found,
            "roaming": roaming.check(hub, ids, found) | {"changes": roaming.assistance(found)},
        }

    @api.post("/{gid}/roaming")
    async def roaming_assistance(gid: str, request: Request) -> StreamingResponse:
        """Roaming assistance: 802.11r with one mobility domain, 802.11k and v on every network
        that is on several members; member by member, each with its rollback."""
        signed_in(request)
        ubus = reachable(gid)
        found = await sync.survey(ubus, salt())
        changes = roaming.assistance(found)
        if not changes:
            raise HTTPException(409, "the networks are set up for roaming already")

        async def work(say):
            outcome = {}
            for id, sections in changes.items():
                say(f"{id}: {', '.join(sections)}")
                try:
                    expected = {
                        e["section"]: n["ssid"]
                        for n in found["networks"]
                        for e in n["on"]
                        if e["device"] == id and e["section"] in sections
                    }
                    await ow.change_wifi(ubus[id], sections, expected_ssids=expected)
                    outcome[id] = "done"
                except Exception as e:
                    outcome[id] = f"failed: {str(e)[:160]}"
                    say(f"{id}: {outcome[id]} (the others are left as they are)")
                    break
                say(f"{id}: done; it answers again")
            return outcome

        return job(gid, "group.roaming", request, work, devices=sorted(changes))

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

    @api.get("/{gid}/ssh")
    async def ssh_files(
        gid: str,
        request: Request,
        file: sshsync.File = "authorized_keys",
        users: Annotated[list[str] | None, Query(max_length=20)] = None,
    ) -> dict[str, Any]:
        """One file of the given users on every member with `ssh:`, side by side: a column per
        device and user ("device/user"), a row per entry."""
        require_admin(request)
        group = group_of(gid)
        users = users or [sshsync.LOGIN]
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; it has no files to read")
        members = {id: d for id in group.devices if (d := hub.config.devices.get(id)) and d.ssh}

        async def one(id: str) -> dict[str, sshsync.Copy | Exception]:
            device = members[id]
            try:
                return await sshsync.fetch(hub.keys, device, file, users)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            except Exception as e:  # the device: unreachable, its key not trusted, ...
                why = RuntimeError(
                    f"{e} (trust the host key first)" if "not trusted" in str(e) else str(e)[:200]
                )
                return dict.fromkeys(sshsync.users_of(device, users), why)

        found = dict(zip(members, await asyncio.gather(*(one(id) for id in members)), strict=True))
        columns, copies = [], {}
        for id, per_user in found.items():
            for user, got in per_user.items():
                column = f"{id}/{user}"
                if isinstance(got, Exception):
                    columns.append(
                        {"id": column, "device": id, "user": user, "error": str(got) or type(got).__name__}
                    )
                else:
                    copies[column] = got
                    columns.append(
                        {
                            "id": column,
                            "device": id,
                            "user": user,
                            "path": got.path,
                            "exists": got.exists,
                            "version": got.version,
                            "login": user == members[id].ssh.user,
                        }
                    )
        return {
            "file": file,
            "users": users,
            "columns": columns,
            "no_ssh": [id for id in group.devices if id not in members],
            "rows": sshsync.merge(file, copies, sshkeys.kastellan_keys(hub.keys)),
        }

    @api.post("/{gid}/ssh/push")
    async def ssh_push(gid: str, body: SshPush, request: Request) -> StreamingResponse:
        """The changes, column by column (a device's user's file), each only if the file is as
        it was fetched. Kastellan's own keys are never removed here."""
        w = signed_in(request)
        group = group_of(gid)
        own = sshkeys.kastellan_keys(hub.keys)
        plan: list[tuple[str, str, Device, ColumnChange]] = []
        for column, change in body.changes.items():
            id, _, user = column.partition("/")
            device = hub.config.devices.get(id)
            if id not in group.devices or device is None or device.ssh is None:
                raise HTTPException(400, f"{column}: not a member with ssh:")
            for line in change.add:
                entry = check(sshsync.check_line, body.file, line)
                # Kastellan's keys go onto devices only through their setup (from=, restrict).
                if body.file == "authorized_keys" and entry.fingerprint in own:
                    raise HTTPException(400, "that is one of Kastellan's own keys; its setup adds it")
            if body.file == "authorized_keys":
                for key in change.remove:
                    kind, _, data = key.partition(" ")
                    if sshsync._fingerprint(kind, data) in own:
                        raise HTTPException(400, "that is one of Kastellan's own keys; rotate it instead")
            if change.add or change.remove:
                plan.append((id, user, device, change))
        if not plan:
            raise HTTPException(400, "nothing to change")
        address = client_address(request.scope)

        async def work(say):
            outcome = {}
            for id, user, device, change in plan:
                column = f"{id}/{user}"
                logged = partial(
                    hub.audit.record,
                    "ssh.sync",
                    w.user,
                    address,
                    group=gid,
                    device=id,
                    account=user,
                    file=body.file,
                )
                try:
                    async with sshkeys.device_lock(id):
                        added, removed = await sshsync.push(
                            hub.keys, device, body.file, user, change.version, change.add, set(change.remove)
                        )
                except Exception as e:
                    why = str(e)[:200] or type(e).__name__
                    say(f"{column}: {why}")
                    logged(outcome=f"failed: {why}")
                    outcome[column] = f"failed: {why}"
                    continue
                summary = f"added {len(added)}, removed {len(removed)}" if added or removed else "unchanged"
                say(f"{column}: {summary}")
                logged(
                    outcome="done",
                    added=[e.fingerprint for e in added],
                    # What an added key may do (not the key): an unrestricted grant shows as such.
                    options=[
                        (sshkeys.parse_line(e.line) or ("",))[0] if body.file == "authorized_keys" else ""
                        for e in added
                    ],
                    removed=[e.fingerprint for e in removed],
                )
                outcome[column] = summary
            return outcome

        return job(
            gid, "group.ssh", request, work, file=body.file, columns=[f"{i}/{u}" for i, u, _, _ in plan]
        )

    return api
