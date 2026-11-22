"""The collector over HTTP (host/collector.py): its install commands, installing it once with a
login and password typed on the page, and what it reports (in /api/devices)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import setup_run
from .access import hub_address, port_of
from .auth_api import client_address, require_admin, require_signin
from .host import collector
from .setup_run import Login
from .ssh import ChangedHost, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")

    last_asked: dict[str, float] = {}

    @api.post("/{id}/collect/now")
    async def collect_now(id: str, request: Request) -> dict[str, Any]:
        """Read the host's collector now, not at the next minute (a page's "read again")."""
        require_admin(request)
        device = setup_run.device_of(hub, id)
        if device.collect is None:
            raise HTTPException(409, "no collector set up there")
        now = asyncio.get_running_loop().time()
        if now - last_asked.get(id, -1e9) < 10:
            raise HTTPException(429, "just read; try again in a few seconds")
        last_asked[id] = now
        try:
            async with asyncio.timeout(60):
                await hub.collect_host(id, device)
        except TimeoutError as e:
            raise HTTPException(504, "the collector took too long") from e
        status = hub.monitor.status.get(id)
        if status and status.host_error:
            raise HTTPException(502, f"the collector: {status.host_error}")
        return {"checked": status.host_checked if status else None}

    @api.get("/{id}/collect")
    async def collect_setup(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device = setup_run.device_of(hub, id)
        port = port_of(device, device.collect)
        hub_ip = await asyncio.to_thread(hub_address, device.host, port)
        status = hub.monitor.status.get(id)
        state = "off"
        if device.collect:
            state = "reading" if status and status.host else "waiting"
            if status and status.host_error:
                state = "failing"
        return {
            "configured": device.collect is not None,
            "state": state,
            "error": status.host_error if status else None,
            "port": port,
            "hub_ip": hub_ip,
            "user": collector.USER,
            "script": collector.install_script(hub.keys.public_key("collect"), hub_ip),
            "uninstall": collector.uninstall_script(),
        }

    @api.post("/{id}/collect/install")
    async def collect_install(id: str, body: Login, request: Request) -> StreamingResponse:
        """Log in once with the typed password, install the collector as root (streaming its
        output), and add `collect:` to the device in config.yaml."""
        w = require_signin(request)
        device = setup_run.device_of(hub, id)
        setup_run.resolve(hub, id, body, w.user)
        hub_ip = await asyncio.to_thread(hub_address, device.host, body.port)
        script = collector.install_script(hub.keys.public_key("collect"), hub_ip)
        audit = partial(
            hub.audit.record,
            "collect.install",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            elevate=body.elevate,
        )
        conn = await setup_run.connect(hub, device.host, body, audit, device=id, user=w.user)

        def after() -> dict[str, Any]:
            # Kastellan reads it from now on: collect: in config.yaml.
            setup_run.add_device_key(hub, w.user, client_address(request.scope), id, "collect", body.port)
            return {}

        return setup_run.stream(conn, script, body, audit, after)

    @api.post("/{id}/collect/uninstall")
    async def collect_uninstall(id: str, body: Login, request: Request) -> StreamingResponse:
        """Log in once with the typed password, take the collector off the host as root
        (streaming its output), and remove `collect:` from the device in config.yaml."""
        w = require_signin(request)
        device = setup_run.device_of(hub, id)
        setup_run.resolve(hub, id, body, w.user)
        audit = partial(
            hub.audit.record,
            "collect.uninstall",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            elevate=body.elevate,
        )
        conn = await setup_run.connect(hub, device.host, body, audit, device=id, user=w.user)

        def after() -> dict[str, Any]:
            setup_run.remove_device_key(hub, w.user, client_address(request.scope), id, "collect")
            return {}

        return setup_run.stream(conn, collector.uninstall_script(), body, audit, after)

    @api.post("/{id}/collect/test")
    async def collect_test(id: str, request: Request) -> dict[str, Any]:
        """Read the collector once now (after installing, or to see why it fails)."""
        require_admin(request)
        device = setup_run.device_of(hub, id)
        if device.collect is None:
            raise HTTPException(409, "no collect: for this device yet")
        try:
            report = await collector.collect(hub.keys, device)
        except (UnknownHost, ChangedHost) as e:
            raise HTTPException(409, f"{e} (trust the host key first)") from e
        except asyncssh.PermissionDenied as e:
            raise HTTPException(502, "the collector's key isn't accepted there (install it first)") from e
        except Exception as e:
            raise HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}") from e
        return {"ok": True, "errors": report.get("errors") or {}}

    return api
