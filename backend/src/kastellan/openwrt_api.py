"""OpenWrt devices over HTTP (openwrt/): setting up Kastellan's login there (the abilities it may
have), and what it does with them: reboot, Wi-Fi settings, firmware. Seeing: admins; changing:
signed-in admins, every change audited."""

from __future__ import annotations

import asyncio
import secrets as random
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import setup_run
from .abilities import ADDED
from .auth_api import client_address, require_admin, require_signin
from .openwrt import client as ow
from .openwrt import firmware as fw
from .openwrt.setup import ABILITIES, install_script, md5crypt
from .pinned_http import ChangedCertificate
from .setup_run import Login

if TYPE_CHECKING:
    from .hub import Hub


class Choice(BaseModel):
    abilities: list[str] = Field(default_factory=list, max_length=len(ABILITIES))
    services: list[str] = Field(default_factory=list, max_length=100)  # with the services ability


class ServiceAction(BaseModel):
    action: Literal["start", "stop", "restart", "enable", "disable"]


class Install(Choice, Login):
    # Renewing what it has (Updates): the abilities left out weren't chosen against.
    renew: bool = False


class WifiChange(BaseModel):
    # section -> option -> value; only the options the page may change are used (client.py).
    changes: dict[str, dict[str, str | int | bool | None]] = Field(max_length=32)


class Upgrade(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")
    upgrading: set[str] = set()  # devices whose firmware is being updated now

    def device_of(id: str, configured: bool = False):
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        if not device.host:  # "" would be this machine: the hub's own
            raise HTTPException(400, f"{id} has no address of its own (a UPS read through its host)")
        if configured and hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is changed")
        if configured and device.openwrt is None:
            raise HTTPException(409, "no openwrt: for this device (set Kastellan's login up first)")
        return device

    def password_for(id: str) -> str:
        """Kastellan's password on the device: made once, kept as a secret, reused by a new
        setup (so a setup that isn't run yet never locks the working one out)."""
        name = hub.openwrt_secret(id)
        try:
            return hub.secrets.get(name)
        except KeyError:
            password = random.token_urlsafe(24)
            hub.secrets.set(name, password)
            return password

    def failed(e: Exception) -> HTTPException:
        if isinstance(e, ow.UbusError) and e.code in (6, ow.DENIED):
            return HTTPException(403, "the device doesn't let Kastellan do that (tick it in the setup)")
        if isinstance(e, ow.UbusError):
            return HTTPException(502, f"the device said: {e}")
        if isinstance(e, ChangedCertificate | ow.LoginRefused):
            return HTTPException(409, str(e))
        if isinstance(e, ValueError):
            return HTTPException(400, str(e))
        if isinstance(e, TimeoutError | httpx.TimeoutException):
            return HTTPException(504, "the device didn't answer in time")
        return HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")

    async def allowed(id: str) -> list[str] | None:
        """The abilities the device grants Kastellan's login now (None: can't tell)."""
        if hub.demo:
            return ["reboot", "services", "wifi"]
        try:
            access = await hub.ubus(id).call("session", "access", {})
        except Exception:
            return None
        groups = access.get("access-group") or {}
        return sorted(a for a in ABILITIES if f"kastellan-{a}" in groups)

    @api.post("/{id}/openwrt/script")
    async def script(id: str, body: Choice, request: Request) -> dict[str, Any]:
        """The commands for a choice of abilities, and what the device grants now."""
        require_admin(request)
        device = device_of(id)
        try:
            text = install_script(set(body.abilities), md5crypt(password_for(id)), body.services)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "configured": device.openwrt is not None,
            "allowed": await allowed(id) if device.openwrt else None,
            "abilities": ABILITIES,
            "port": device.ssh.port if device.ssh else 22,
            "script": text,
        }

    @api.post("/{id}/openwrt/install")
    async def install(id: str, body: Install, request: Request) -> StreamingResponse:
        """Set the login up on the device (as root over SSH, with a password typed once)."""
        w = require_signin(request)
        device = device_of(id, configured=hub.demo)
        setup_run.resolve(hub, id, body, w.user)
        try:
            text = install_script(set(body.abilities), md5crypt(password_for(id)), body.services)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        body.elevate = "none" if body.login == "root" else body.elevate
        audit = partial(
            hub.audit.record,
            "openwrt.install",
            w.user,
            client_address(request.scope),
            device=id,
            login=body.login,
            abilities=sorted(body.abilities),
            services=sorted(body.services) if "services" in body.abilities else [],
        )
        conn = await setup_run.connect(hub, device.host, body, audit, device=id, user=w.user)
        output: list[str] = []

        def after() -> dict[str, Any]:
            fp = ow.fingerprint_from_setup(output)
            if fp is None:
                raise ValueError("the setup didn't show the web server's certificate")
            hub.pins.set(id, fp)  # read on the device itself, over SSH with a checked host key
            hub.drop_clients(id, fritz=False)  # a fresh session
            setup_run.add_device_key(hub, w.user, client_address(request.scope), id, "openwrt", 443, {})
            if not body.renew:
                hub.offered.passed(id, "openwrt", set(ADDED["openwrt"]) - set(body.abilities))
            return {"certificate": fp}

        return setup_run.stream(conn, text, body, audit, after, keep=output)

    @api.post("/{id}/openwrt/reboot")
    async def reboot(id: str, request: Request) -> dict[str, Any]:
        w = require_signin(request)
        device_of(id, configured=True)
        audit = partial(hub.audit.record, "openwrt.reboot", w.user, client_address(request.scope), device=id)
        try:
            await ow.reboot(hub.ubus(id))
        except Exception as e:
            audit(outcome=f"failed: {type(e).__name__}")
            raise failed(e) from e
        audit(outcome="done")
        return {"rebooting": True}

    @api.post("/{id}/openwrt/services/{name}")
    async def service(id: str, name: str, body: ServiceAction, request: Request) -> dict[str, Any]:
        """Act on a service the device's allow list names (the device checks it)."""
        w = require_signin(request)
        device_of(id, configured=True)
        audit = partial(
            hub.audit.record,
            "openwrt.service",
            w.user,
            client_address(request.scope),
            device=id,
            service=name,
            action=body.action,
        )
        try:
            out = await ow.service(hub.ubus(id), name, body.action)
        except Exception as e:
            audit(outcome=f"failed: {str(e)[:120] or type(e).__name__}")
            raise failed(e) from e
        audit(outcome="done")
        return {"output": out}

    @api.get("/{id}/roaming")
    async def roaming_view(id: str, request: Request) -> dict[str, Any]:
        """How clients roam at this access point (usteer, the `roaming` ability): its nodes and
        the others it hears of, its clients (named from the DHCP leases), its thresholds."""
        require_admin(request)
        device_of(id)
        r = hub.roaming.get(id)
        if r is None:
            status = hub.monitor.status.get(id)
            able = "roaming" in ((status.router_abilities if status else None) or [])
            return {"read": False, "why": "not read yet" if able else "needs the roaming ability"}
        names: dict[str, str] = {}
        for st in hub.monitor.status.values():
            for lease in (((st.router or {}).get("dhcp") or {}).get("leases") or []) if st else []:
                if lease.get("mac") and lease.get("name"):
                    names[lease["mac"]] = lease["name"]
        hosts = {}
        for other, d in hub.config.devices.items():
            if d.host:
                hosts[hub.resolver.address(d.host) or d.host] = other
        return {
            "read": True,
            **r,
            "nodes": [n | {"device": hosts.get(n.get("host") or "")} for n in r.get("nodes") or []],
            "clients": [c | {"name": names.get(c["mac"])} for c in r.get("clients") or []],
        }

    @api.get("/{id}/openwrt/wifi")
    async def wifi(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device_of(id, configured=True)
        try:
            return await ow.wifi_settings(hub.ubus(id))
        except Exception as e:
            raise failed(e) from e

    @api.put("/{id}/openwrt/wifi")
    async def change_wifi(id: str, body: WifiChange, request: Request) -> dict[str, Any]:
        """Change Wi-Fi settings; applied with a rollback the device does by itself if it stops
        answering. Keys aren't logged."""
        w = require_signin(request)
        device_of(id, configured=True)
        logged = {
            s: {k: ("***" if k == "key" else v) for k, v in vals.items()} for s, vals in body.changes.items()
        }
        audit = partial(
            hub.audit.record, "openwrt.wifi", w.user, client_address(request.scope), device=id, changes=logged
        )
        try:
            await ow.change_wifi(hub.ubus(id), body.changes)
        except Exception as e:
            audit(outcome=f"failed: {str(e)[:120] or type(e).__name__}")
            raise failed(e) from e
        audit(outcome="done")
        return await ow.wifi_settings(hub.ubus(id))

    @api.post("/{id}/openwrt/firmware")
    async def upgrade(id: str, body: Upgrade, request: Request) -> StreamingResponse:
        """Build an image of `version` for the device (OpenWrt's image builder, with its
        packages), check it, put it on the device, let it test it, and start the upgrade, keeping
        the settings. Streamed; runs to its end even if the page goes."""
        w = require_signin(request)
        device_of(id, configured=True)
        status = hub.monitor.status.get(id)
        report = (status.router if status else None) or {}
        advice = report.get("firmware") or {}
        board = report.get("board") or {}
        release = board.get("release") or {}
        allowed_versions = {advice.get("next_step"), advice.get("newest_in_series")} - {None}
        if body.version not in allowed_versions:
            raise HTTPException(
                400, f"one step at a time: now {', '.join(sorted(allowed_versions)) or 'nothing'}"
            )
        if not (release.get("target") and board.get("board_name")):
            raise HTTPException(409, "the device's model isn't known yet")
        if id in upgrading:
            raise HTTPException(409, "a firmware update of this device is already under way")
        upgrading.add(id)
        audit = partial(
            hub.audit.record,
            "openwrt.firmware",
            w.user,
            client_address(request.scope),
            device=id,
            version=body.version,
            current=release.get("version"),
        )

        async def work(say):
            ubus = hub.ubus(id)
            model = f"{board.get('model')} ({release.get('target')})"
            say(f"{model}: OpenWrt {release.get('version')} -> {body.version}")
            installed = sorted(
                (await ubus.call("rpc-sys", "packagelist", {"all": False})).get("packages", {})
            )
            packages = await renamed(installed, body.version)
            say(f"{len(packages)} installed packages go along")
            image, sha, name = await fw.build_image(
                release["target"],
                board["board_name"],
                body.version,
                packages,
                say,
                filesystem=board.get("rootfs_type"),
            )
            say(f"{name}: {len(image) / 1e6:.1f} MB, checksum right; putting it on the device")
            await ow.install_firmware(ubus, image, sha, say)
            say("the device took the image and restarts with it (keeping its settings); a few minutes")
            for _ in range(60):  # up to 10 minutes
                await asyncio.sleep(10)
                try:
                    now = await hub.ubus(id).call("system", "board", timeout=8)
                except Exception:
                    continue
                version = (now.get("release") or {}).get("version")
                if version == body.version:
                    say(f"back, running OpenWrt {version}")
                    return {"version": version}
            raise TimeoutError("it didn't come back with the new version within 10 minutes; check it")

        async def once(say):
            try:
                return await work(say)
            finally:
                upgrading.discard(id)

        return setup_run.stream_work(once, audit)

    async def renamed(packages: list[str], version: str) -> list[str]:
        """Packages renamed between series (OpenWrt's image builder lists them)."""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                overview = (await c.get(f"{fw.ASU}/json/v1/overview.json")).json()
            branch = overview["branches"][version.rsplit(".", 1)[0]]
        except Exception:
            return packages
        out = set(packages)
        for change in branch.get("package_changes") or []:
            source, target = change.get("source"), change.get("target")
            if source in out:
                out.discard(source)
                if target:
                    out.add(target)
        return sorted(out)

    return api
