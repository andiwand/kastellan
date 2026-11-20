"""VPN access over HTTP: who has access (WireGuard through wg-easy, OpenVPN through easy-rsa),
giving it, taking it away, and the client's config to download.

Seeing the lists is for anyone signed in; creating, revoking and downloading (a config holds the
client's private key) need a signed-in admin and go to the audit log.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import asyncssh
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .auth_api import client_address, require_signin
from .ssh import ChangedHost, Target, UnknownHost
from .vpn.openvpn import NAME, OpenVpn
from .vpn.wireguard import WgEasy

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


class NewClient(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    days: int | None = Field(default=None, ge=1, le=3650)  # None: wg-easy never; OpenVPN: the default


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/vpn")

    def wireguard() -> WgEasy:
        wg = hub.config.vpn.wireguard
        if wg is None:
            raise HTTPException(404, "no vpn: wireguard: in config.yaml")
        try:
            password = hub.secrets.get(wg.password_secret)
        except KeyError as e:
            raise HTTPException(409, str(e.args[0])) from e
        return WgEasy(wg.wg_easy, wg.user, password)

    def openvpn() -> OpenVpn:
        ovpn = hub.config.vpn.openvpn
        if ovpn is None:
            raise HTTPException(404, "no vpn: openvpn: in config.yaml")

        def target(id: str) -> Target:
            device = hub.config.devices[id]
            assert device.ssh is not None  # checked by the config
            return Target(device.host, device.ssh.port, device.ssh.user)

        return OpenVpn(hub.keys, target(ovpn.pki), target(ovpn.server) if ovpn.server else None)

    def problem(e: Exception) -> HTTPException:
        """A failure of wg-easy or the PKI host, said plainly (never with credentials)."""
        if isinstance(e, HTTPException):
            return e
        if isinstance(e, httpx.HTTPStatusError):
            code = e.response.status_code
            return HTTPException(502, "wg-easy refused the login" if code == 401 else f"wg-easy: HTTP {code}")
        if isinstance(e, (UnknownHost, ChangedHost)):
            return HTTPException(409, f"{e} (trust it on the device's terminal page)")
        if isinstance(e, asyncssh.PermissionDenied):
            return HTTPException(502, "the PKI host doesn't accept the hub's key")
        if isinstance(e, (ValueError, RuntimeError)):
            return HTTPException(400, str(e))
        return HTTPException(502, f"{type(e).__name__}: {e}")

    def audit(request: Request, action: str, **details: Any) -> None:
        w = require_signin(request)
        hub.audit.record(action, w.user, client_address(request.scope), **details)

    @api.get("")
    async def overview() -> dict[str, Any]:
        out: dict[str, Any] = {"wireguard": None, "openvpn": None}
        if hub.config.vpn.wireguard:
            try:
                out["wireguard"] = {"clients": [asdict(c) for c in await wireguard().clients()]}
            except Exception as e:
                out["wireguard"] = {"clients": [], "error": problem(e).detail}
        if hub.config.vpn.openvpn:
            try:
                out["openvpn"] = {
                    "clients": [asdict(c) for c in await openvpn().clients()],
                    "crl_to_server": hub.config.vpn.openvpn.server is not None,
                }
            except Exception as e:
                out["openvpn"] = {"clients": [], "error": problem(e).detail}
        return out

    # --- WireGuard -------------------------------------------------------------------------------

    @api.post("/wireguard")
    async def wg_create(body: NewClient, request: Request) -> dict[str, str]:
        require_signin(request)
        expires = datetime.now(UTC) + timedelta(days=body.days) if body.days else None
        try:
            id = await wireguard().create(body.name, expires)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.wireguard.create", client=body.name, id=id, days=body.days)
        return {"id": id}

    @api.post("/wireguard/{id}/{action}")
    async def wg_toggle(id: str, action: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        if action not in ("enable", "disable"):
            raise HTTPException(404, "enable or disable")
        try:
            await wireguard().enable(id, action == "enable")
        except Exception as e:
            raise problem(e) from e
        audit(request, f"vpn.wireguard.{action}", id=id)
        return {"enabled": action == "enable"}

    @api.delete("/wireguard/{id}")
    async def wg_delete(id: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        try:
            await wireguard().delete(id)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.wireguard.delete", id=id)
        return {"deleted": True}

    @api.get("/wireguard/{id}/config")
    async def wg_config(id: str, request: Request) -> Response:
        require_signin(request)
        try:
            text = await wireguard().configuration(id)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.wireguard.download", id=id)
        return _download(text, f"wireguard-{id}.conf")

    @api.get("/wireguard/{id}/qr")
    async def wg_qr(id: str, request: Request) -> Response:
        require_signin(request)
        try:
            svg = await wireguard().qrcode(id)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.wireguard.qr", id=id)
        # An SVG from wg-easy is shown as an image only, never as a page (no script runs).
        return Response(
            svg,
            media_type="image/svg+xml",
            headers=_private | {"Content-Security-Policy": "script-src 'none'"},
        )

    # --- OpenVPN ---------------------------------------------------------------------------------

    @api.post("/openvpn")
    async def ovpn_issue(body: NewClient, request: Request) -> dict[str, str]:
        require_signin(request)
        if not NAME.match(body.name):
            raise HTTPException(400, "names are lowercase letters, digits, . _ - (up to 64)")
        days = body.days or (hub.config.vpn.openvpn.days if hub.config.vpn.openvpn else 365)
        try:
            await openvpn().issue(body.name, days)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.openvpn.issue", client=body.name, days=days)
        return {"name": body.name}

    @api.post("/openvpn/{name}/revoke")
    async def ovpn_revoke(name: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        try:
            pushed = await openvpn().revoke(name)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.openvpn.revoke", client=name, crl_pushed=pushed)
        return {"revoked": True, "crl_pushed": pushed}

    @api.get("/openvpn/{name}/config")
    async def ovpn_config(name: str, request: Request) -> Response:
        require_signin(request)
        try:
            text = await openvpn().export(name)
        except Exception as e:
            raise problem(e) from e
        audit(request, "vpn.openvpn.download", client=name)
        return _download(text, f"{name}.ovpn")

    return api


_private = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _download(text: str, filename: str) -> Response:
    """A config with a private key in it: a download, never cached."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "client.conf"
    headers = _private | {"Content-Disposition": f'attachment; filename="{safe}"'}
    return Response(text, media_type="application/octet-stream", headers=headers)
