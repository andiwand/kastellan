"""Central logs over HTTP (logs.py): the lines with filters, what there is to filter by, and
where they come from. Admins only (logs hold names, addresses and what people did); a search
someone makes goes to the audit log (its filters, never the lines)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from .auth_api import client_address, require_admin, require_signin
from .logs import LEVELS

if TYPE_CHECKING:
    from .hub import Hub


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/logs")
    access = require_admin if hub.demo else require_signin

    @api.get("")
    async def lines(
        request: Request,
        response: Response,
        device: Annotated[list[str] | None, Query()] = None,
        source: Annotated[list[str] | None, Query()] = None,
        program: Annotated[list[str] | None, Query()] = None,
        level: int = Query(default=7, ge=0, le=7),
        since: float | None = Query(default=None, allow_inf_nan=False),
        until: float | None = Query(default=None, allow_inf_nan=False),
        q: str | None = Query(default=None, max_length=200),
        before: int | None = Query(default=None, ge=0, le=2**63 - 1),
        after: int | None = Query(default=None, ge=0, le=2**63 - 1),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        w = access(request)
        response.headers["Cache-Control"] = "no-store"
        device, source, program = device or [], source or [], program or []
        if len(device) > 100 or len(source) > 20 or len(program) > 100:
            raise HTTPException(400, "too many filters")
        rows = await asyncio.to_thread(
            hub.logs.query,
            device or None,
            source or None,
            program or None,
            level,
            since,
            until,
            q,
            before,
            after,
            limit,
        )
        if request.headers.get("x-kastellan-auto") != "1" and after is None:
            hub.audit.record(
                "logs.read",
                w.user,
                client_address(request.scope),
                devices=device,
                sources=source,
                programs=program,
                level=LEVELS[level],
                text=q or "",
                since=since,
                until=until,
            )
        return {"lines": rows, "levels": LEVELS}

    @api.get("/facets")
    async def facets(request: Request, hours: int = Query(default=24, ge=1, le=24 * 365)) -> dict[str, Any]:
        access(request)
        return await asyncio.to_thread(hub.logs.facets, time.time() - hours * 3600)

    @api.get("/sources")
    async def sources(request: Request) -> dict[str, Any]:
        """Per device: how its lines come in and how that went; and the syslog port."""
        access(request)
        return {
            "devices": {id: {k: v for k, v in s.items() if k != "tried"} for id, s in hub.log_status.items()},
            "syslog": hub.syslog.view() | {"enabled": hub.config.logs.syslog.enabled},
            "syslog_tls": hub.syslog_tls.view() | {"enabled": hub.config.logs.syslog_tls.enabled},
            "days": hub.config.logs.days,
            "max_lines": hub.config.logs.max_lines,
        }

    @api.get("/syslog/{id}")
    async def syslog_state(id: str, request: Request) -> dict[str, Any]:
        """Whether a device has a certificate to send syslog over TLS, and where to send it."""
        require_admin(request)
        if id not in hub.config.devices:
            raise HTTPException(404, "no such device")
        return await asyncio.to_thread(hub.syslog_ca.state, id) | {
            "enabled": hub.config.logs.syslog_tls.enabled,
            "port": hub.config.logs.syslog_tls.port,
            "listening": hub.syslog_tls.server is not None,
        }

    @api.post("/syslog/{id}/certificate")
    async def certificate(id: str, request: Request, response: Response) -> dict[str, Any]:
        """A client certificate for the device, with its key (shown once, never kept)."""
        w = require_signin(request)
        if id not in hub.config.devices:
            raise HTTPException(404, "no such device")
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is issued")
        try:
            out = await asyncio.to_thread(hub.syslog_ca.issue, id)
        except Exception as e:
            raise HTTPException(
                503, f"can't issue a certificate: {(str(e) or type(e).__name__)[:200]}"
            ) from e
        response.headers["Cache-Control"] = "no-store"
        hub.audit.record("syslog.certificate", w.user, client_address(request.scope), device=id)
        return out | {"port": hub.config.logs.syslog_tls.port}

    @api.delete("/syslog/{id}/certificate")
    async def revoke(id: str, request: Request) -> dict[str, Any]:
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is revoked")
        n = await asyncio.to_thread(hub.syslog_ca.revoke, id)
        hub.audit.record("syslog.revoke", w.user, client_address(request.scope), device=id, certificates=n)
        return {"revoked": n}

    return api
