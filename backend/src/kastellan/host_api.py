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
from .access import USER, hub_address
from .auth_api import client_address, require_admin, require_signin
from .host import collector
from .setup_run import Login
from .ssh import ChangedHost, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")

    def device_of(id: str):
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        return device

    @api.get("/{id}/collect")
    async def collect_setup(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device = device_of(id)
        port = (device.collect.port if device.collect else None) or (device.ssh.port if device.ssh else 22)
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
        device = device_of(id)
        if not USER.match(body.login):
            raise HTTPException(400, "user names are letters, digits, . _ - (up to 32)")
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
        conn = await setup_run.connect(hub, device.host, body, audit)

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
        device = device_of(id)
        if not USER.match(body.login):
            raise HTTPException(400, "user names are letters, digits, . _ - (up to 32)")
        audit = partial(
            hub.audit.record,
            "collect.uninstall",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            elevate=body.elevate,
        )
        conn = await setup_run.connect(hub, device.host, body, audit)

        def after() -> dict[str, Any]:
            setup_run.remove_device_key(hub, w.user, client_address(request.scope), id, "collect")
            return {}

        return setup_run.stream(conn, collector.uninstall_script(), body, audit, after)

    @api.post("/{id}/collect/test")
    async def collect_test(id: str, request: Request) -> dict[str, Any]:
        """Read the collector once now (after installing, or to see why it fails)."""
        require_admin(request)
        device = device_of(id)
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
