"""The HTTP API under /api, and the web app (a single-page app) for every other path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, checks, stats
from . import ups as upsmod
from .access_api import router as access_router
from .act_api import router as act_router
from .alarms import overdue, report_fresh
from .alarms_api import router as alarms_router
from .auth_api import Guard, HubHeaders, client_address, require_admin
from .auth_api import router as auth_router
from .backups_api import router as backups_router
from .config import Check, Device, Kind
from .config_api import router as config_router
from .credentials import router as credentials_router
from .desktop_api import router as desktop_router
from .devbackups_api import router as devbackups_router
from .discovery import router as discovery_router
from .fritzbox_api import router as fritzbox_router
from .groups_api import router as groups_router
from .host import collector
from .host_api import router as host_router
from .hub import Hub
from .hubbackup_api import router as hubbackup_router
from .internet_api import router as internet_router
from .logs_api import router as logs_router
from .monitor import CheckStatus, DeviceStatus
from .networks import INTERNET, Network, network_of, networks_of, public_ways
from .networks import Hub as NetHub
from .openwrt_api import router as openwrt_router
from .push_api import router as push_router
from .settings import Settings
from .signins_api import router as signins_router
from .skipped import DEVICE_KINDS, device_offers
from .skipped import identity as skipped_identity
from .skipped import offer as skipped_offer
from .sshkeys_api import router as sshkeys_router
from .terminal_api import router as terminal_router
from .terminal_api import ssh_port
from .tls import public_name
from .vault import Damaged, Locked, NotForKastellan
from .vault_api import router as vault_router
from .vpn_api import router as vpn_router
from .web_setup import router as web_setup_router
from .weblogin import router as weblogin_router
from .webproxy import Access as WebAccess
from .webproxy import WebProxy
from .webproxy import router as web_router
from .webproxy import upstream as web_upstream
from .wifi_api import router as wifi_router

WEB = Path(__file__).parent / "web"

# How far back; NaN and infinity are refused (timedelta can't take them).
Hours = Annotated[float, Query(gt=0, allow_inf_nan=False)]


def version_event(e: dict[str, Any]) -> dict[str, Any]:
    """A software change from versions.log, as an event among the ups and downs."""
    return {"kind": "version", **{k: e[k] for k in ("time", "device", "check", "from", "to")}}


def outdated_scripts(device: Device, status: DeviceStatus | None, vpn: list[str] | None = None) -> list[str]:
    """Kastellan's scripts on the host that are older than this hub's: set up again to renew."""
    out = []
    if status and device.collect is not None and collector.outdated(status.host):
        out.append("collector")
    if status and status.actor_outdated:
        out.append("actor")
    if status and device.openwrt is not None and status.router_outdated:
        out.append("openwrt")
    out += vpn or []
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

    @app.exception_handler(Locked)
    async def locked(request: Request, exc: Locked) -> JSONResponse:
        # Anything that needs a secret while the key ring is locked: say so, never a 500.
        return JSONResponse({"detail": str(exc)}, status_code=423)

    @app.exception_handler(NotForKastellan)
    async def not_for_kastellan(request: Request, exc: NotForKastellan) -> JSONResponse:
        # config.yaml names a person's login where Kastellan would use it by itself.
        return JSONResponse({"detail": f"{exc}; name one in a ring Kastellan may use"}, status_code=409)

    @app.exception_handler(Damaged)
    async def damaged(request: Request, exc: Damaged) -> JSONResponse:
        return JSONResponse({"detail": f"{exc} (see the Keys page)"}, status_code=409)

    app.add_middleware(Guard, hub=hub)
    app.add_middleware(HubHeaders)  # outside the Guard: its refusals carry them too
    web_access = WebAccess()
    app.add_middleware(
        WebProxy, hub=hub, access=web_access
    )  # outermost: a device's name never reaches the hub's routes
    app.include_router(web_router(hub, web_access))
    app.include_router(weblogin_router(hub, web_access))
    app.include_router(auth_router(hub))
    app.include_router(vault_router(hub))
    app.include_router(terminal_router(hub))
    app.include_router(desktop_router(hub))
    app.include_router(alarms_router(hub))
    app.include_router(backups_router(hub))
    app.include_router(logs_router(hub))
    app.include_router(devbackups_router(hub))
    app.include_router(hubbackup_router(hub))
    app.include_router(fritzbox_router(hub))
    app.include_router(internet_router(hub))
    app.include_router(wifi_router(hub))
    app.include_router(signins_router(hub))
    app.include_router(discovery_router(hub))
    app.include_router(credentials_router(hub))
    app.include_router(web_setup_router(hub))
    app.include_router(push_router(hub))
    app.include_router(vpn_router(hub))
    app.include_router(access_router(hub))
    app.include_router(config_router(hub))
    app.include_router(sshkeys_router(hub))
    app.include_router(host_router(hub))
    app.include_router(act_router(hub))
    app.include_router(openwrt_router(hub))
    app.include_router(groups_router(hub))

    def vault_waiting() -> list[str]:
        try:
            rings = hub.vault.ring_ids()
            return [r for r in rings if not hub.vault.is_open(r) and hub.vault.policy(r).background]
        except Damaged:
            return []

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
            "vault": hub.vault.state,  # absent | locked | partly | unlocked | damaged (vault.py)
            # Locked rings Kastellan uses by itself: what needs their secrets waits.
            "vault_waiting": vault_waiting(),
            # A newer Kastellan, if one is out (releases.py): {version, url}, or None.
            "newer": asdict(r) if (r := hub.newer_release()) else None,
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
        if status.checked and overdue(status.checked, interval, now):
            view |= {"up": None, "detail": "no recent result", "stale": True}
        return view

    def hub_network() -> Network | None:
        return network_of(hub.config, hub.own_address)

    def hub_place() -> NetHub:
        return NetHub(hub_network(), hub.own_v6)

    def where(device: Device, status: Any) -> dict[str, Any]:
        """Which networks the device is in, and its ways in from the internet (networks.py)."""
        if not device.host and device.ups and device.ups.via in hub.config.devices:
            # A UPS on a host's USB has no address: it is where that host is, with no way in.
            via = hub.config.devices[device.ups.via]
            return {**where(via, hub.monitor.status.get(device.ups.via)), "address": None, "public_ways": []}
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

    def vms_of(id: str) -> list[dict[str, Any]] | None:
        """A host's VMs as its collector reports them, each with the device that runs in it."""
        status = hub.monitor.status.get(id)
        vms = (status.host or {}).get("vms") if status else None
        if vms is None:
            return None
        guests = {
            d.runs_on.vm: gid for gid, d in hub.config.devices.items() if d.runs_on and d.runs_on.host == id
        }
        return [{**v, "device": guests.get(v.get("name"))} for v in vms]

    def runs_on(device: Device) -> dict[str, Any] | None:
        """Where a virtual machine device runs, and its state there ("" when not reported)."""
        if device.runs_on is None:
            return None
        host = device.runs_on.host
        st = hub.monitor.status.get(host)
        fresh = st is not None and report_fresh(st.host_checked, hub.started, datetime.now(UTC))
        vms = vms_of(host) if fresh else None
        vm = next((v for v in vms or [] if v.get("name") == device.runs_on.vm), None)
        return {"host": host, "vm": device.runs_on.vm, "state": (vm or {}).get("state") or ""}

    def ups_view(id: str, device: Device) -> dict[str, Any]:
        """A UPS's reading and what it powers; for others, the UPS they hang on and the UPSes they
        read (their host's)."""
        status = hub.monitor.status
        now = datetime.now(UTC)
        out: dict[str, Any] = {"powered_by": None, "ups": None, "powers": [], "reads_ups": []}
        if device.kind is Kind.UPS:
            r = upsmod.reading(hub.config, status, id, hub.ups_switched)
            out["ups"] = {**r, "state": upsmod.current_state(r, now)} if r else None
            out["powers"] = upsmod.powered(hub.config, id)
        if device.powered_by:
            r = upsmod.reading(hub.config, status, device.powered_by, hub.ups_switched)
            out["powered_by"] = {"ups": device.powered_by, "state": upsmod.current_state(r, now)}
        for uid, u in hub.config.devices.items():
            if u.ups and u.ups.via == id:
                r = upsmod.reading(hub.config, status, uid, hub.ups_switched)
                out["reads_ups"].append({"device": uid, "state": upsmod.current_state(r, now)})
        return out

    @app.get("/api/devices")
    def devices() -> list[dict[str, Any]]:
        out = []
        with_cert = hub.syslog_ca.devices()
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
                    "ssh_port": ssh_port(device),  # where it answers SSH (ssh:, or an SSH check)
                    "desktop": device.desktop.model_dump(include={"protocol", "user"})
                    if device.desktop
                    else None,
                    "state": status.state if status else "unknown",
                    "driver": device.driver,
                    "info": status.info if status else None,
                    "containers": hub.skipped.mark(id, status.containers) if status else None,
                    "services_since": hub.services.since(id),
                    "updates_skipped": hub.skipped.device_skipped(id, status.router, status.host)
                    if status
                    else [],
                    "containers_checked": status.containers_checked if status else None,
                    "containers_error": status.containers_error if status else None,
                    "vulns": status.vulns if status else None,
                    "collect": device.collect is not None,
                    "act": device.act is not None,
                    "openwrt": device.openwrt is not None,
                    "router": status.router if status else None,
                    "router_checked": status.router_checked if status else None,
                    "router_error": status.router_error if status else None,
                    "fritzbox": device.fritzbox is not None,
                    "syslog_cert": id in with_cert,
                    "fritz": status.fritz if status else None,
                    "fritz_checked": status.fritz_checked if status else None,
                    "fritz_error": status.fritz_error if status else None,
                    "host_report": status.host if status else None,
                    "host_forecast": status.host_forecast if status else None,
                    "vitals": status.vitals if status else None,
                    # libvirt's VMs on the host, from its collector (None: it can't tell)
                    "vms": vms_of(id),
                    "runs_on": runs_on(device),
                    **ups_view(id, device),
                    "host_checked": status.host_checked if status else None,
                    "host_error": status.host_error if status else None,
                    "outdated_scripts": []
                    if hub.demo
                    else outdated_scripts(device, status, hub.vpn_outdated_on(id)),
                    "new_abilities": {
                        kind: new
                        for kind, configured, installed in (
                            ("actor", device.act, status.actor_abilities if status else None),
                            ("openwrt", device.openwrt, status.router_abilities if status else None),
                        )
                        if configured is not None and (new := hub.offered.new(id, kind, installed))
                    },
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

    @app.post("/api/devices/{id}/abilities/{kind}/not-needed")
    def not_needed(id: str, kind: str, request: Request) -> dict[str, Any]:
        """New abilities of the actor or OpenWrt login there: not wanted (Updates stops asking)."""
        w = require_admin(request)
        status = hub.monitor.status.get(id)
        if id not in hub.config.devices or kind not in ("actor", "openwrt") or status is None:
            raise HTTPException(404, "no such device or kind")
        installed = status.actor_abilities if kind == "actor" else status.router_abilities
        new = hub.offered.new(id, kind, installed)
        hub.offered.passed(id, kind, new)
        hub.audit.record(
            "abilities.not-needed", w.user, client_address(request.scope), device=id, kind=kind, abilities=new
        )
        return {"passed": new}

    @app.post("/api/devices/{id}/containers/{key:path}/not-needed")
    def update_not_needed(id: str, key: str, request: Request) -> dict[str, Any]:
        """A container's update offer: not wanted (Updates stops showing it until a newer one)."""
        w = require_admin(request)
        status = hub.monitor.status.get(id)
        c = next(
            (c for c in (status.containers if status else None) or [] if c.get("key", c["name"]) == key), None
        )
        what = skipped_offer(c) if c else None
        if what is None:
            raise HTTPException(404, "no update offered for that container")
        hub.skipped.skip(id, key, skipped_identity(c))
        hub.audit.record(
            "update.not-needed", w.user, client_address(request.scope), device=id, container=key, update=what
        )
        return {"skipped": what}

    @app.post("/api/devices/{id}/updates/{kind}/not-needed")
    def device_update_not_needed(id: str, kind: str, request: Request) -> dict[str, Any]:
        """The device's firmware step, wanted reboot or system updates as they are now: not
        wanted (Updates stops showing it until it changes)."""
        w = require_admin(request)
        status = hub.monitor.status.get(id)
        what = device_offers(status.router, status.host).get(kind) if status else None
        if kind not in DEVICE_KINDS or what is None:
            raise HTTPException(404, "no such update offered for that device")
        hub.skipped.skip(id, f"@{kind}", what)
        hub.audit.record(
            "update.not-needed", w.user, client_address(request.scope), device=id, kind=kind, update=what
        )
        return {"skipped": what}

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
        out = {"start": start, "step_seconds": step.total_seconds(), "devices": data}
        if device is not None:  # a device's page: its own services too (own_services.py)
            own = stats.series(hub.services.history.read(start, device=device), start, step, buckets)
            out["services"] = own.get(device, {})
        return out

    @app.get("/api/hub/resources")
    async def hub_resources(request: Request) -> dict[str, Any]:
        """What Kastellan costs the machine: its CPU and memory, and its data folder store by
        store, with where each levels off (footprint.py). Admins: it shows the folder's layout."""
        require_admin(request)
        return await asyncio.to_thread(hub.resources.report, hub)

    @app.get("/api/vitals")
    async def vitals_series(
        hours: Hours = 24, buckets: int = 48, device: str | None = None
    ) -> dict[str, Any]:
        """CPU, memory and storage (percent) and temperature (°C), the mean in each of `buckets`
        slices of the last `hours`: one device's (charts), or every device's (the list's trends)."""
        hours = min(max(hours, 1), 24 * 30)
        buckets = min(max(buckets, 1), 500)
        if device is not None and device not in hub.config.devices:
            raise HTTPException(404, "no such device")
        step = hours * 3600 / buckets
        start = datetime.now(UTC) - timedelta(hours=hours)
        devices = [device] if device else list(hub.config.devices)
        data = await asyncio.to_thread(hub.vitals.series, devices, start.timestamp(), step, buckets)
        return {"start": start, "step_seconds": step, "devices": data}

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
