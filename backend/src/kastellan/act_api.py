"""The actor over HTTP (host/actor.py): what a host lets Kastellan do, the commands to set that
up (for a chosen set of abilities and containers), installing it once with a password, and the
actions themselves. Changing anything: signed-in admins, every action audited."""

from __future__ import annotations

import asyncio
import contextlib
import json
from functools import partial
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import setup_run
from .abilities import ADDED
from .access import hub_address, port_of
from .auth_api import client_address, require_admin, require_signin
from .host import actor
from .registry import NEWER_BUILD
from .setup_run import Login
from .ssh import ChangedHost, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


class Choice(BaseModel):
    abilities: list[str] = Field(default_factory=list, max_length=len(actor.ABILITIES))
    containers: list[str] = Field(default_factory=list, max_length=200)  # ["*"] for all
    vms: list[str] = Field(default_factory=list, max_length=200)  # ["*"] for all
    allowed: dict[str, list[str]] | None = Field(default=None, max_length=len(actor.ABILITIES))


class Install(Choice, Login):
    # Renewing what it has (Updates): the abilities left out weren't chosen against.
    renew: bool = False


class Action(BaseModel):
    container: str | None = Field(default=None, max_length=128)
    vm: str | None = Field(default=None, max_length=128)
    unit: str | None = Field(default=None, max_length=210)
    cursor: str | None = Field(default=None, max_length=400)  # journal, docker-logs: where to go on
    tag: str | None = Field(default=None, max_length=128)  # update-container: the tag to go to


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")

    def failed(e: Exception) -> HTTPException:
        if isinstance(e, UnknownHost | ChangedHost):
            return HTTPException(409, f"{e} (trust the host key first)")
        if isinstance(e, asyncssh.PermissionDenied):
            return HTTPException(502, "the act key isn't accepted there (set up the actor first)")
        if isinstance(e, ValueError):
            return HTTPException(400, str(e))
        if isinstance(e, TimeoutError | asyncssh.TimeoutError):
            return HTTPException(504, "timed out: check the host")
        return HTTPException(502, str(e)[:300] or type(e).__name__)

    def script_for(choice: Choice, hub_ip: str | None) -> str:
        try:
            return actor.install_script(
                set(choice.abilities),
                choice.containers,
                hub.keys.public_key("act"),
                hub_ip,
                vms=choice.vms,
                allowed=choice.allowed,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @api.post("/{id}/act/script")
    async def act_script(id: str, body: Choice, request: Request) -> dict[str, Any]:
        """The commands for a choice of abilities (to copy), and what the host allows now."""
        require_admin(request)
        device = setup_run.device_of(hub, id)
        port = port_of(device, device.act)
        hub_ip = await asyncio.to_thread(hub_address, device.host, port)
        now: dict[str, list[str]] | None = None
        error = None
        if device.act is not None:
            try:
                now = await actor.abilities(hub.keys, device)
            except Exception as e:
                error = failed(e).detail
        return {
            "configured": device.act is not None,
            "allowed": now,
            "error": error,
            "abilities": actor.ABILITIES,
            "port": port,
            "script": script_for(body, hub_ip),
            "uninstall": actor.uninstall_script(),
        }

    @api.post("/{id}/act/install")
    async def act_install(id: str, body: Install, request: Request) -> StreamingResponse:
        """Set the host up for exactly this choice (with a login and password typed once),
        streaming the output, and add `act:` to the device in config.yaml."""
        w = require_signin(request)
        device = setup_run.device_of(hub, id)
        setup_run.resolve(hub, id, body, w.user)
        hub_ip = await asyncio.to_thread(hub_address, device.host, body.port)
        script = script_for(body, hub_ip)
        audit = partial(
            hub.audit.record,
            "act.install",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            elevate=body.elevate,
            abilities=sorted(body.abilities),
            containers=body.containers,
            vms=body.vms,
            allowed=body.allowed,
        )
        conn = await setup_run.connect(hub, device.host, body, audit, device=id, user=w.user)

        def after() -> dict[str, Any]:
            setup_run.add_device_key(hub, w.user, client_address(request.scope), id, "act", body.port)
            # What was offered and left out at this setup was a choice: not new any more.
            if not body.renew:
                hub.offered.passed(id, "actor", set(ADDED["actor"]) - set(body.abilities))
            # Its scripts are new now: ask again, not in up to ten minutes (Updates says "older").
            setup_run.spawn(hub.poll_actor(id))
            return {}

        return setup_run.stream(conn, script, body, audit, after)

    @api.post("/{id}/act/uninstall")
    async def act_uninstall(id: str, body: Login, request: Request) -> StreamingResponse:
        """Take the actor off the host (with a login typed once), streaming the output, and
        remove `act:` from the device in config.yaml."""
        w = require_signin(request)
        device = setup_run.device_of(hub, id)
        setup_run.resolve(hub, id, body, w.user)
        audit = partial(
            hub.audit.record,
            "act.uninstall",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            elevate=body.elevate,
        )
        conn = await setup_run.connect(hub, device.host, body, audit, device=id, user=w.user)

        def after() -> dict[str, Any]:
            setup_run.remove_device_key(hub, w.user, client_address(request.scope), id, "act")
            return {}

        return setup_run.stream(conn, actor.uninstall_script(), body, audit, after)

    def action(id: str, ability: str, body: Action, request: Request):
        w = require_signin(request)
        device = setup_run.device_of(hub, id)
        if device.act is None:
            raise HTTPException(409, "no act: for this device (set up the actor first)")
        if ability not in actor.ABILITIES:
            raise HTTPException(404, "no such ability")
        # The ability says what it acts on: a VM, a unit, a container, where to read from, or
        # nothing (reboot). Any other field set is refused, not ignored, so the audit line
        # always names what was acted on.
        field = (
            "vm"
            if ability in actor.VM_ABILITIES
            else "unit"
            if ability in actor.UNIT_ABILITIES
            else "container"
            if ability in actor.CONTAINER_ABILITIES
            else "cursor"
            if ability in actor.CURSOR_ABILITIES
            else None
        )
        given = {k: v for k, v in body.model_dump().items() if v is not None}
        tag = given.pop("tag", None)
        if set(given) - {field} or (tag is not None and ability != "update-container"):
            takes = f"a {field}, nothing else" if field else "nothing"
            raise HTTPException(400, f"{ability} takes {takes}")
        target = given.get(field) if field else None
        if tag is not None:
            # Only a tag the hub found for this container (its newer version, or the newest
            # within its major one): the page can't send the host to any tag it likes.
            status = hub.monitor.status.get(id)
            matches = [c for c in (status.containers if status else None) or [] if c.get("name") == target]
            if len(matches) != 1:
                raise HTTPException(409, "the container isn't uniquely identified on this device")
            offered = {
                t
                for c in matches
                if c.get("update") and not c.get("unchecked")
                for t in (c.get("update_to"), c.get("update_within"))
                if t and t != NEWER_BUILD
            }
            if tag not in offered:
                raise HTTPException(409, f"{tag} isn't a newer version Kastellan found for {target}")
        audit = partial(
            hub.audit.record,
            f"act.{ability}",
            w.user,
            client_address(request.scope),
            device=id,
            **({field: target} if target is not None else {}),
            **({"tag": tag} if tag is not None else {}),
        )
        return device, target, tag, audit

    def brief(result: dict[str, Any]) -> dict[str, Any]:
        """For the audit: what was done, never what was read (log lines)."""
        return {k: v for k, v in result.items() if k != "lines"}

    async def look_again(id: str, device) -> None:
        """The host as it is now (a container stopped, a VM started), before the page asks."""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(30):
                await hub.collect_host(id, device)

    @api.post("/{id}/act/{ability}/stream")
    async def act_streamed(id: str, ability: str, body: Action, request: Request) -> StreamingResponse:
        """The same, with how far it got as it goes: `KASTELLAN-STEP <text>` lines, then
        `KASTELLAN-DONE {"ok": ..., "result" | "error": ...}`. The hub's task, not the page's:
        it finishes (and is audited) when the browser leaves halfway."""
        device, target, tag, audit = action(id, ability, body, request)
        audit(outcome="started")
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def said(text: str) -> None:
            queue.put_nowait(f"{actor.STEP}{text}\n".encode())

        async def run() -> None:
            try:
                result = await actor.act(hub.keys, device, ability, target, on_step=said, tag=tag)
                outcome: dict[str, Any] = {"ok": True, "result": result}
                audit(outcome="done", result=brief(result))
            except BaseException as e:  # also when the hub stops: the audit still says so
                if not isinstance(e, Exception):
                    audit(outcome=f"interrupted: {type(e).__name__}")
                    queue.put_nowait(None)
                    raise
                outcome = {"ok": False, "error": failed(e).detail}
                audit(outcome=f"failed: {str(e)[:200] or type(e).__name__}")
            if ability not in actor.READING:
                said("reading the host again")
                await look_again(id, device)
            queue.put_nowait(f"{setup_run.DONE} {json.dumps(outcome)}\n".encode())
            queue.put_nowait(None)

        return setup_run.hub_task(run(), queue)

    @api.post("/{id}/act/{ability}")
    async def act_now(id: str, ability: str, body: Action, request: Request) -> dict[str, Any]:
        """Do one thing on the host, as far as it allows."""
        device, target, tag, audit = action(id, ability, body, request)
        try:
            result = await actor.act(hub.keys, device, ability, target, tag=tag)
            audit(outcome="done", result=brief(result))
        except asyncio.CancelledError:
            audit(outcome="interrupted: CancelledError")
            raise
        except Exception as e:
            audit(outcome=f"failed: {str(e)[:200] or type(e).__name__}")
            raise failed(e) from e
        finally:
            if ability not in actor.READING:
                await look_again(id, device)
        return result

    return api
