"""The HTTP API under /api, and the web app (a single-page app) for every other path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from . import __version__, checks, stats
from .auth_api import Guard
from .auth_api import router as auth_router
from .config import Check
from .desktop_api import router as desktop_router
from .hub import Hub
from .monitor import CheckStatus
from .settings import Settings
from .terminal_api import router as terminal_router

WEB = Path(__file__).parent / "web"


def create_app(settings: Settings, hub: Hub | None = None) -> FastAPI:
    hub = hub or Hub(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await hub.start()
        yield
        await hub.stop()

    app = FastAPI(
        title="Kastellan",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.hub = hub
    app.add_middleware(Guard, hub=hub)
    app.include_router(auth_router(hub))
    app.include_router(terminal_router(hub))
    app.include_router(desktop_router(hub))

    @app.get("/api/health")
    def health(request: Request) -> dict[str, Any]:
        # Public: what's wrong is only for admins (config errors name hosts and settings).
        w = request.scope.get("state", {}).get("who")
        trusted = w is not None and w.is_admin and not hub.locked
        problem = hub.locked or hub.config_error
        return {
            "ok": not hub.locked,
            "version": __version__,
            "locked": bool(hub.locked),
            "config_error": (problem if trusted else "config.yaml has a problem") if problem else None,
            "ping_refused": checks.ping_refused if trusted else None,
            "demo": hub.demo is not None,
        }

    def check_view(check: Check, status: CheckStatus | None) -> dict[str, Any]:
        view: dict[str, Any] = {"name": check.name, "type": check.type, "target": check.target}
        if status is None:
            return view
        view |= vars(status)
        # A result much older than the interval means the loop is stuck: say so, don't show the
        # old answer as current.
        interval = check.interval or hub.config.check_interval
        if status.checked and (datetime.now(UTC) - status.checked).total_seconds() > 3 * interval + 60:
            view |= {"up": None, "detail": "no recent result", "stale": True}
        return view

    @app.get("/api/devices")
    def devices() -> list[dict[str, Any]]:
        out = []
        for id, device in hub.config.devices.items():
            status = hub.monitor.status.get(id)
            out.append(
                {
                    "id": id,
                    "name": device.name,
                    "host": device.host,
                    "kind": device.kind,
                    "parent": device.parent,
                    "tags": device.tags,
                    "always_on": device.always_on,
                    "ssh": device.ssh.model_dump() if device.ssh else None,
                    "desktop": device.desktop.model_dump(include={"protocol", "user"})
                    if device.desktop
                    else None,
                    "state": status.state if status else "unknown",
                    "checks": [
                        check_view(check, status.checks.get(check.name) if status else None)
                        for check in device.checks
                    ],
                }
            )
        return out

    @app.get("/api/devices/{id}/history")
    def history(id: str, hours: float = 24) -> list[dict[str, Any]]:
        if id not in hub.config.devices:
            raise HTTPException(404, "no such device")
        since = datetime.now(UTC) - timedelta(hours=min(hours, 24 * 90))
        return [vars(row) for row in hub.history.read(since, device=id)]

    @app.get("/api/series")
    def series(hours: float = 24, buckets: int = 48, device: str | None = None) -> dict[str, Any]:
        """Uptime and mean latency in `buckets` equal slices of the last `hours`, per device and check."""
        hours = min(max(hours, 0.25), 24 * 90)
        buckets = min(max(buckets, 1), 500)
        step = timedelta(hours=hours) / buckets
        start = datetime.now(UTC) - timedelta(hours=hours)
        data = stats.series(hub.history.read(start, device=device), start, step, buckets)
        return {"start": start, "step_seconds": step.total_seconds(), "devices": data}

    @app.get("/api/changes")
    def changes(hours: float = 24, device: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """When checks went up or down, and when a service's software changed."""
        since = datetime.now(UTC) - timedelta(hours=min(hours, 24 * 90))
        # Ups and downs, and software updates ("OpenSSH 9.8 → 9.9"), newest first.
        events = stats.changes(hub.history.read(since, device=device))
        events += [
            {
                "kind": "version",
                "time": e["time"],
                "device": e["device"],
                "check": e["check"],
                "from": e["from"],
                "to": e["to"],
            }
            for e in hub.versions.changes(since, device)
        ]
        events.sort(key=lambda e: e["time"], reverse=True)
        return events[:limit]

    @app.get("/api/{path:path}")
    def no_api(path: str) -> None:
        raise HTTPException(404, "no such API")

    @app.get("/{path:path}")
    def web(path: str) -> FileResponse:
        if not WEB.exists():
            raise HTTPException(404, "the web app is not built; run `npm run dev` in frontend/")
        file = (WEB / path).resolve()
        if path and file.is_file() and file.is_relative_to(WEB.resolve()):
            return FileResponse(file)
        return FileResponse(WEB / "index.html")

    return app
