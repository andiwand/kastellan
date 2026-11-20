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

from .auth_api import client_address, require_signin, who
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

    async def attempt(request: Request, action: str, work: Any, **details: Any) -> Any:
        """Run a change and audit it, failed attempts too (with what went wrong)."""
        try:
            result = await work
        except Exception as e:
            error = problem(e)
            audit(request, action, outcome=f"failed: {error.detail}", **details)
            raise error from e
        audit(request, action, outcome="done", **details)
        return result

    @api.get("")
    async def overview(request: Request) -> dict[str, Any]:
        # Error details (hosts, what failed) are for admins; others learn only that it failed.
        admin = who(request).is_admin

        def say(e: Exception, what: str) -> str:
            return str(problem(e).detail) if admin else f"{what} can't be reached right now"

        out: dict[str, Any] = {"wireguard": None, "openvpn": None}
        wg = hub.config.vpn.wireguard
        if wg:
            url = httpx.URL(wg.wg_easy)
            out["wireguard"] = {
                "clients": [],
                # Basic auth over plain HTTP to another machine: the password crosses the network.
                "insecure": url.scheme == "http" and url.host not in ("localhost", "127.0.0.1", "::1"),
            }
            try:
                out["wireguard"]["clients"] = [asdict(c) for c in await wireguard().clients()]
            except Exception as e:
                out["wireguard"]["error"] = say(e, "wg-easy")
        if hub.config.vpn.openvpn:
            out["openvpn"] = {
                "clients": [],
                "crl_to_server": hub.config.vpn.openvpn.server is not None,
                "crl": hub.crl_state,
            }
            try:
                out["openvpn"]["clients"] = [asdict(c) for c in await openvpn().clients()]
            except Exception as e:
                out["openvpn"]["error"] = say(e, "The PKI host")
        return out

    # --- WireGuard -------------------------------------------------------------------------------

    @api.post("/wireguard")
    async def wg_create(body: NewClient, request: Request) -> dict[str, str]:
        require_signin(request)
        expires = datetime.now(UTC) + timedelta(days=body.days) if body.days else None
        id = await attempt(
            request,
            "vpn.wireguard.create",
            wireguard().create(body.name, expires),
            client=body.name,
            days=body.days,
        )
        return {"id": id}

    @api.post("/wireguard/{id}/{action}")
    async def wg_toggle(id: str, action: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        if action not in ("enable", "disable"):
            raise HTTPException(404, "enable or disable")
        await attempt(request, f"vpn.wireguard.{action}", wireguard().enable(id, action == "enable"), id=id)
        return {"enabled": action == "enable"}

    @api.delete("/wireguard/{id}")
    async def wg_delete(id: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        await attempt(request, "vpn.wireguard.delete", wireguard().delete(id), id=id)
        return {"deleted": True}

    @api.get("/wireguard/{id}/config")
    async def wg_config(id: str, request: Request) -> Response:
        require_signin(request)
        text = await attempt(request, "vpn.wireguard.download", wireguard().configuration(id), id=id)
        return _download(text, f"wireguard-{id}.conf")

    @api.get("/wireguard/{id}/qr")
    async def wg_qr(id: str, request: Request) -> Response:
        require_signin(request)
        svg = await attempt(request, "vpn.wireguard.qr", wireguard().qrcode(id), id=id)
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
        await attempt(
            request, "vpn.openvpn.issue", openvpn().issue(body.name, days), client=body.name, days=days
        )
        return {"name": body.name}

    @api.post("/openvpn/{name}/revoke")
    async def ovpn_revoke(name: str, request: Request) -> dict[str, Any]:
        require_signin(request)
        ovpn = openvpn()
        await attempt(request, "vpn.openvpn.revoke", ovpn.revoke(name), client=name)
        # The revoke stands (and is in the audit log) even if the server can't be reached now;
        # the page then offers to push the CRL again, and the daily renewal does too.
        try:
            await attempt(request, "vpn.openvpn.crl", hub.push_crl(ovpn), after=f"revoking {name}")
        except HTTPException as e:
            return {"revoked": True, "crl_pushed": False, "error": e.detail}
        return {"revoked": True, "crl_pushed": True}

    @api.post("/openvpn/crl")
    async def ovpn_push_crl(request: Request) -> dict[str, Any]:
        require_signin(request)
        await attempt(request, "vpn.openvpn.crl", hub.push_crl(openvpn(), renew=True))
        return {"crl": hub.crl_state}

    @api.get("/openvpn/{name}/config")
    async def ovpn_config(name: str, request: Request) -> Response:
        require_signin(request)
        text = await attempt(request, "vpn.openvpn.download", openvpn().export(name), client=name)
        return _download(text, f"{name}.ovpn")

    return api


_private = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _download(text: str, filename: str) -> Response:
    """A config with a private key in it: a download, never cached."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "client.conf"
    headers = _private | {"Content-Disposition": f'attachment; filename="{safe}"'}
    return Response(text, media_type="application/octet-stream", headers=headers)
