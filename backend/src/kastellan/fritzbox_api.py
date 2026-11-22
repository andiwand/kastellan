"""FRITZ!Boxes over HTTP (fritzbox/): setting up Kastellan's login there (a FRITZ!Box user and the
certificate confirmed by an admin), and what it does with it: reboot, firmware update, Wi-Fi,
WPS off. Changing: signed-in admins, every change audited (never a password)."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import setup_run
from .auth_api import client_address, require_admin, require_signin
from .fritzbox import client as fb
from .pinned_http import ChangedCertificate

if TYPE_CHECKING:
    from .hub import Hub


class Port(BaseModel):
    port: int = Field(default=49443, ge=1, le=65535)


class Setup(Port):
    user: str = Field(pattern=r"^[A-Za-z0-9_.@-]{1,64}$")
    password: str = Field(min_length=1, max_length=128)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")  # the one the admin compared and confirmed


class _Confirmed:
    """In place of Pins for a trial login: the one fingerprint an admin confirmed, not yet kept."""

    def __init__(self, fingerprint: str) -> None:
        self.fingerprint = fingerprint

    def get(self, id: str) -> str | None:
        return self.fingerprint


class WifiChange(BaseModel):
    ssid: str | None = Field(default=None, max_length=64)
    key: str | None = Field(default=None, max_length=64)
    channel: int | None = Field(default=None, ge=0, le=233)  # 0: automatic
    enabled: bool | None = None


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")

    def device_of(id: str, configured: bool = False) -> Any:
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        if not device.host:
            raise HTTPException(400, f"{id} has no address of its own")
        if configured and hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is changed")
        if configured and device.fritzbox is None:
            raise HTTPException(409, "no fritzbox: for this device (set Kastellan's login up first)")
        return device

    def failed(e: Exception) -> HTTPException:
        if isinstance(e, fb.FritzError):
            return HTTPException(403 if e.code in (606, 866, 867) else 502, str(e))
        if isinstance(e, fb.LoginRefused | ChangedCertificate):
            return HTTPException(409, str(e))
        if isinstance(e, ValueError):
            return HTTPException(400, str(e))
        if isinstance(e, TimeoutError):
            return HTTPException(504, "the FRITZ!Box didn't answer in time")
        return HTTPException(502, f"{type(e).__name__}: {str(e)[:200]}")

    @api.post("/{id}/fritzbox/certificate")
    async def certificate(id: str, body: Port, request: Request) -> dict[str, Any]:
        """The fingerprint of the certificate the FRITZ!Box shows now, to compare with the one
        FRITZ!OS shows (Internet → Permit Access → FRITZ!Box Services)."""
        require_admin(request)
        device = device_of(id)
        if hub.demo:
            raise HTTPException(409, "demo mode has no device certificate to read")
        try:
            fp = await fb.certificate(device.host, body.port)
        except Exception as e:
            raise HTTPException(502, f"no HTTPS on {device.host}:{body.port}: {setup_run.reason(e)}") from e
        return {"fingerprint": fp, "pinned": hub.fritz_pins.get(id), "port": body.port}

    @api.post("/{id}/fritzbox/setup")
    async def setup(id: str, body: Setup, request: Request) -> dict[str, Any]:
        """Keep the user's password (as the secret fritzbox-<id>) and the confirmed certificate,
        check that the login works, and write `fritzbox:` in config.yaml."""
        w = require_signin(request)
        device = device_of(id)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is set up")
        audit = partial(
            hub.audit.record,
            "fritzbox.setup",
            w.user,
            client_address(request.scope),
            device=id,
            user=body.user,
        )
        try:
            now = await fb.certificate(device.host, body.port)
        except Exception as e:
            raise HTTPException(502, f"no HTTPS on {device.host}:{body.port}: {setup_run.reason(e)}") from e
        if now != body.fingerprint:
            audit(outcome="refused: another certificate")
            raise HTTPException(409, "the FRITZ!Box shows another certificate than the one confirmed")
        # A failed or cancelled trial must never replace the live pin.
        trial = fb.Fritz(id, device.host, body.port, body.user, body.password, _Confirmed(body.fingerprint))
        try:
            info = await trial.call("DeviceInfo:1", "GetInfo")
        except Exception as e:
            audit(outcome=f"failed: {type(e).__name__}")
            raise failed(e) from e
        finally:
            trial.pool.close()
        name = (device.fritzbox.password_secret if device.fritzbox else None) or f"fritzbox-{id}"
        if hub.config.devices.get(id) != device:
            raise HTTPException(409, "the device changed during setup; try again")
        value: dict[str, Any] = {"user": body.user} | ({} if body.port == 49443 else {"port": body.port})
        if device.fritzbox and device.fritzbox.password_secret:
            value["password_secret"] = device.fritzbox.password_secret
        from . import config_edit

        # Validate before replacing live credentials; restore the previous login if saving fails.
        old_config = config_edit.read(hub.settings.config)
        hub.check(config_edit.set_device_key(old_config, id, "fritzbox", value))
        old_password = hub.secrets.get(name) if hub.secrets.has(name) else None
        old_pin = hub.fritz_pins.get(id)
        try:
            hub.secrets.set(name, body.password)
            hub.fritz_pins.set(id, body.fingerprint)
            setup_run.add_device_key(
                hub, w.user, client_address(request.scope), id, "fritzbox", body.port, value, replace=True
            )
        except Exception:
            if old_password is not None:
                hub.secrets.set(name, old_password)
            if old_pin is None:
                hub.fritz_pins.forget(id)
            else:
                hub.fritz_pins.set(id, old_pin)
            audit(outcome="failed to save setup")
            raise
        hub.drop_clients(id, ubus=False)
        audit(outcome="done")
        return {"model": info.get("NewModelName"), "firmware": info.get("NewSoftwareVersion")}

    @api.delete("/{id}/fritzbox")
    async def remove(id: str, request: Request) -> dict[str, Any]:
        """Forget the login (the FRITZ!Box user stays: remove it in FRITZ!OS)."""
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode: nothing is removed")
        device = device_of(id)
        if device.fritzbox is None:
            raise HTTPException(404, "no FRITZ!Box login for this device")
        from . import config_edit

        old = config_edit.read(hub.settings.config)
        new = config_edit.set_device_key(old, id, "fritzbox", None)
        hub.check(new)
        hub.save_config(old, new)
        hub.fritz_pins.forget(id)
        hub.drop_clients(id, ubus=False)
        hub.audit.record("fritzbox.remove", w.user, client_address(request.scope), device=id)
        return {"removed": True}

    async def act(id: str, request: Request, what: str, work, **logged: Any) -> dict[str, Any]:
        w = require_signin(request)
        device_of(id, configured=True)
        audit = partial(
            hub.audit.record, f"fritzbox.{what}", w.user, client_address(request.scope), device=id
        )
        try:
            out = await work(hub.fritz(id))
        except Exception as e:
            audit(outcome=f"failed: {str(e)[:120] or type(e).__name__}", **logged)
            raise failed(e) from e
        audit(outcome="done", **logged)
        return out or {"done": True}

    @api.post("/{id}/fritzbox/reboot")
    async def reboot(id: str, request: Request) -> dict[str, Any]:
        return await act(id, request, "reboot", fb.reboot)

    @api.post("/{id}/fritzbox/firmware")
    async def firmware(id: str, request: Request) -> dict[str, Any]:
        return await act(id, request, "firmware", fb.update_firmware)

    @api.post("/{id}/fritzbox/wps-off")
    async def wps(id: str, request: Request) -> dict[str, Any]:
        return await act(id, request, "wps-off", fb.wps_off)

    @api.put("/{id}/fritzbox/wifi/{index}")
    async def wifi(id: str, index: int, body: WifiChange, request: Request) -> dict[str, Any]:
        values = body.model_dump(exclude_none=True)
        logged = {k: ("***" if k == "key" else v) for k, v in values.items()}

        # The change shows with the next poll (`changing` forgets what the client kept).
        async def work(box: fb.Fritz) -> None:
            await fb.change_wifi(box, index, values)

        return await act(id, request, "wifi", work, network=index, changes=logged)

    return api
