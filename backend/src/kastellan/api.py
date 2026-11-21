"""The HTTP API under /api, and the web app (a single-page app) for every other path."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, checks, stats
from .access_api import router as access_router
from .act_api import router as act_router
from .alarms_api import router as alarms_router
from .auth_api import Guard
from .auth_api import router as auth_router
from .config import Check, Device
from .config_api import router as config_router
from .desktop_api import router as desktop_router
from .groups_api import router as groups_router
from .host import collector
from .host_api import router as host_router
from .hub import Hub
from .monitor import CheckStatus, DeviceStatus
from .networks import INTERNET, Network, network_of, networks_of, public_ways
from .networks import Hub as NetHub
from .openwrt_api import router as openwrt_router
from .push_api import router as push_router
from .settings import Settings
from .sshkeys_api import router as sshkeys_router
from .terminal_api import router as terminal_router
from .tls import public_name
from .vpn_api import router as vpn_router
from .webproxy import Access as WebAccess
from .webproxy import WebProxy
from .webproxy import router as web_router
from .webproxy import upstream as web_upstream

WEB = Path(__file__).parent / "web"

# How far back; NaN and infinity are refused (timedelta can't take them).
Hours = Annotated[float, Query(allow_inf_nan=False)]


def version_event(e: dict[str, Any]) -> dict[str, Any]:
    """A software change from versions.log, as an event among the ups and downs."""
    return {"kind": "version", **{k: e[k] for k in ("time", "device", "check", "from", "to")}}


def outdated_scripts(device: Device, status: DeviceStatus | None) -> list[str]:
    """Kastellan's scripts on the host that are older than this hub's: set up again to renew."""
    out = []
    if status and device.collect is not None and collector.outdated(status.host):
        out.append("collector")
    if status and status.actor_outdated:
        out.append("actor")
    return out


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

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Which field and why, never what was sent: it may be a password.
        detail = [{k: e[k] for k in ("loc", "msg", "type") if k in e} for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    app.add_middleware(Guard, hub=hub)
    web_access = WebAccess()
    app.add_middleware(
        WebProxy, hub=hub, access=web_access
    )  # outermost: a device's name never reaches the hub's routes
    app.include_router(web_router(hub, web_access))
    app.include_router(auth_router(hub))
    app.include_router(terminal_router(hub))
    app.include_router(desktop_router(hub))
    app.include_router(alarms_router(hub))
    app.include_router(push_router(hub))
    app.include_router(vpn_router(hub))
    app.include_router(access_router(hub))
    app.include_router(config_router(hub))
    app.include_router(sshkeys_router(hub))
    app.include_router(host_router(hub))
    app.include_router(act_router(hub))
    app.include_router(openwrt_router(hub))
    app.include_router(groups_router(hub))

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
            # Device web pages through the hub, at <device>.<this> (webproxy.py).
            "web_domain": hub.config.web.domain if hub.config.web else None,
        }

    def check_view(check: Check, status: CheckStatus | None) -> dict[str, Any]:
        view: dict[str, Any] = {
            "name": check.name,
            "type": check.type,
            "target": check.target,
            "exposed": check.exposed,
        }
        if status is None:
            return view
        view |= vars(status)
        now = datetime.now(UTC)
        if status.cert:
            cert = status.cert.view(now)
            days, watched = cert["days_left"], check.watch_cert
            # What the alarm rules make of it (alarms.py), for the page's colours.
            soon = days is not None and days < hub.config.alarms.cert_days
            cert |= {"watched": watched, "problem": watched and (not cert["valid"] or soon)}
            view["cert"] = cert
        # A result much older than the interval means the loop is stuck: say so, don't show the
        # old answer as current.
        interval = check.interval or hub.config.check_interval
        if status.checked and (now - status.checked).total_seconds() > 3 * interval + 60:
            view |= {"up": None, "detail": "no recent result", "stale": True}
        return view

    def hub_network() -> Network | None:
        return network_of(hub.config, hub.own_address)

    def hub_place() -> NetHub:
        return NetHub(hub_network(), hub.own_v6)

    def where(device: Device, status: Any) -> dict[str, Any]:
        """Which networks the device is in, and its ways in from the internet (networks.py)."""
        address = hub.resolver.address(device.host)
        nets = networks_of(hub.config, device, address, hub.gateway, hub_place())
        assumed = False
        if not nets and not public_name(device.host) and (own := hub_network()):
            # A name only the network knows, which the hub couldn't resolve: most likely the
            # hub's own network.
            nets, assumed = [own], True
        return {
            "address": address,
            "networks": [n.view() for n in nets],
            "network_assumed": assumed,
            "public_ways": public_ways(
                device, any(n.id == INTERNET for n in nets), status.checks if status else {}
            ),
        }

    @app.get("/api/networks")
    def networks() -> dict[str, Any]:
        """The hub's own network (where Kastellan runs), and the ones named in config.yaml."""
        own = hub_network()
        return {
            "hub": own.view() if own else None,
            "hub_address": hub.own_address,
            "gateway": hub.gateway,
            "named": [
                {"id": id, "name": n.name or id, "cidr": n.cidr} for id, n in hub.config.networks.items()
            ],
        }

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
                    "site": hub.config.site_of(id),
                    "site_name": hub.config.site_name(hub.config.site_of(id)),
                    "parent": device.parent,
                    "tags": device.tags,
                    "always_on": device.always_on,
                    "ssh": device.ssh.model_dump() if device.ssh else None,
                    "desktop": device.desktop.model_dump(include={"protocol", "user"})
                    if device.desktop
                    else None,
                    "state": status.state if status else "unknown",
                    "driver": device.driver,
                    "info": status.info if status else None,
                    "containers": status.containers if status else None,
                    "containers_checked": status.containers_checked if status else None,
                    "containers_error": status.containers_error if status else None,
                    "vulns": status.vulns if status else None,
                    "collect": device.collect is not None,
                    "act": device.act is not None,
                    "openwrt": device.openwrt is not None,
                    "router": status.router if status else None,
                    "router_checked": status.router_checked if status else None,
                    "router_error": status.router_error if status else None,
                    "host_report": status.host if status else None,
                    "host_checked": status.host_checked if status else None,
                    "host_error": status.host_error if status else None,
                    "outdated_scripts": [] if hub.demo else outdated_scripts(device, status),
                    "exposed": device.exposed,
                    "web_url": web_upstream(device),
                    **where(device, status),
                    "checks": [
                        check_view(check, status.checks.get(check.name) if status else None)
                        for check in device.checks
                    ],
                }
            )
        return out

    @app.get("/api/devices/{id}/history")
    def history(id: str, hours: Hours = 24) -> list[dict[str, Any]]:
        if id not in hub.config.devices:
            raise HTTPException(404, "no such device")
        since = datetime.now(UTC) - timedelta(hours=min(hours, 24 * 90))
        return [vars(row) for row in hub.history.read(since, device=id)]

    @app.get("/api/series")
    def series(hours: Hours = 24, buckets: int = 48, device: str | None = None) -> dict[str, Any]:
        """Uptime and mean latency in `buckets` equal slices of the last `hours`, per device and check."""
        hours = min(max(hours, 0.25), 24 * 90)
        buckets = min(max(buckets, 1), 500)
        step = timedelta(hours=hours) / buckets
        start = datetime.now(UTC) - timedelta(hours=hours)
        data = stats.series(hub.history.read(start, device=device), start, step, buckets)
        return {"start": start, "step_seconds": step.total_seconds(), "devices": data}

    @app.get("/api/activity")
    def activity(hours: Hours = 24, limit: int = 60) -> list[dict[str, Any]]:
        """The day as a person would tell it (stats.activity): outages and how long, short drops
        counted, many devices at once as one, when devices that may be off were on; and software
        updates."""
        now = datetime.now(UTC)
        since = now - timedelta(hours=min(hours, 24 * 7))
        rows = [r for r in hub.history.read(since) if r.device in hub.config.devices]
        on = {id: bool(d.always_on) for id, d in hub.config.devices.items()}
        short = timedelta(seconds=hub.config.alarms.down_after)
        events = stats.activity(rows, on, now, short, gap=short)
        events += [version_event(e) | {"end": e["time"]} for e in hub.versions.changes(since, None)]
        events.sort(key=lambda e: e.get("end") or e.get("last") or now, reverse=True)
        return events[:limit]

    @app.get("/api/changes")
    def changes(hours: Hours = 24, device: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """When checks went up or down, and when a service's software changed."""
        since = datetime.now(UTC) - timedelta(hours=min(hours, 24 * 90))
        # Ups and downs, and software updates ("OpenSSH 9.8 → 9.9"), newest first.
        events = stats.changes(hub.history.read(since, device=device))
        events += [version_event(e) for e in hub.versions.changes(since, device)]
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
