"""VPN access over HTTP: who has access (WireGuard through wg-easy, OpenVPN through easy-rsa),
giving it, taking it away, and the client's config to download.

Seeing the lists is for anyone signed in; creating, revoking and downloading (a config holds the
client's private key) need a signed-in admin and go to the audit log.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import asyncssh
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .access import hub_address
from .auth_api import client_address, require_admin, require_signin, who
from .setup_run import reason
from .ssh import ChangedHost, Target, UnknownHost
from .vpn.openvpn import NAME, OpenVpn, install_script
from .vpn.wireguard import WgEasy

if TYPE_CHECKING:
    from .config import Device
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
        devices = hub.config.devices
        return OpenVpn(hub.keys, ovpn, devices[ovpn.pki], devices[ovpn.server] if ovpn.server else None)

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

    # --- Setting up OpenVPN on the hosts --------------------------------------------------------

    def roles() -> list[tuple[str, str, str]]:
        """(role, device id, the restricted login) for the PKI host and the server."""
        ovpn = hub.config.vpn.openvpn
        if ovpn is None:
            raise HTTPException(404, "no vpn: openvpn: in config.yaml")
        out = [("pki", ovpn.pki, ovpn.pki_user)]
        if ovpn.server:
            out.append(("server", ovpn.server, ovpn.server_user))
        return out

    async def run(
        target: Target, which: str, command: str, input: str = "", timeout: float = 20
    ) -> tuple[int, str, str]:
        """One command; stdin always ends (a probe must never wait for input), time-limited."""
        async with await hub.keys.connect(target, which) as conn:
            # Nothing to send: stdin closed at once (asyncssh leaves it open for input="").
            stdin = {"input": input} if input else {"stdin": asyncssh.DEVNULL}
            r = await conn.run(command, check=False, timeout=timeout, **stdin)
        return r.exit_status or 0, str(r.stdout or ""), str(r.stderr or "")

    async def hub_ip_for(device: Device) -> str | None:
        return await asyncio.to_thread(hub_address, device.host, device.ssh.port if device.ssh else 22)

    async def checks(role: str, id: str, user: str) -> list[dict[str, Any]]:
        """What works now on one host, tried for real."""
        ovpn = hub.config.vpn.openvpn
        assert ovpn is not None
        device = hub.config.devices[id]
        port = device.ssh.port if device.ssh else 22
        out: list[dict[str, Any]] = []

        def note(name: str, ok: bool, detail: str = "") -> bool:
            out.append({"name": name, "ok": ok, "detail": detail})
            return ok

        try:
            await hub.keys.check_host(Target(device.host, port, user))
            note("Host key trusted", True)
        except UnknownHost as e:
            note("Host key trusted", False, f"trust {e.fingerprint} on the device's terminal page")
            return out
        except ChangedHost as e:
            note("Host key trusted", False, str(e))
            return out
        except Exception as e:
            note("Host key trusted", False, f"can't reach {device.host}: {reason(e)}")
            return out
        if ovpn.mode == "restricted":
            # Ask the installed script something harmless; its answer proves it is bound to the key.
            command = "list" if role == "pki" else "status"
            try:
                code, _, err = await run(Target(device.host, port, user), "vpn", command)
                answers = code == 0 if role == "pki" else "only 'install'" in err
                note(
                    f"kastellan-{role} answers for {user}",
                    answers,
                    "" if answers else (err.strip()[-200:] or f"exit {code}"),
                )
            except asyncssh.PermissionDenied:
                note(
                    f"kastellan-{role} answers for {user}",
                    False,
                    f"{user}@{device.host} doesn't accept Kastellan's VPN key yet",
                )
            except Exception as e:
                note(f"kastellan-{role} answers for {user}", False, f"{type(e).__name__}: {e}")
        if device.ssh:
            # The normal login: needed for direct mode and for installing.
            try:
                code, out_id, _ = await run(
                    Target(device.host, port, device.ssh.user),
                    "hub",
                    "id -u; sudo -n true 2>/dev/null && echo sudo",
                )
                root = out_id.split()[0:1] == ["0"] or "sudo" in out_id.split()
                uses = "for direct mode and installing" if ovpn.mode == "direct" else "for installing"
                note(
                    f"Normal login {device.ssh.user} ({uses})",
                    code == 0,
                    "can install (root or passwordless sudo)"
                    if root
                    else "can't install: not root, no passwordless sudo",
                )
                out[-1]["can_install"] = code == 0 and root
            except Exception as e:
                note(f"Normal login {device.ssh.user}", False, f"{type(e).__name__}: {e}")
        return out

    @api.get("/openvpn/setup")
    async def setup(request: Request) -> dict[str, Any]:
        require_admin(request)
        found_roles = roles()  # 404 without openvpn:
        ovpn = hub.config.vpn.openvpn
        assert ovpn is not None
        key = hub.keys.public_key("vpn")
        hosts = []
        for role, id, user in found_roles:
            device = hub.config.devices[id]
            found = await checks(role, id, user)
            hosts.append(
                {
                    "role": role,
                    "device": id,
                    "name": device.name,
                    "host": device.host,
                    "user": user,
                    "login": device.ssh.user if device.ssh else None,
                    "checks": found,
                    "can_install": any(c.get("can_install") for c in found),
                    "script": install_script(role, ovpn, key, await hub_ip_for(device)),
                }
            )
        return {"mode": ovpn.mode, "vpn_key": key, "hosts": hosts}

    @api.post("/openvpn/setup/{role}/install")
    async def install(role: str, request: Request) -> dict[str, Any]:
        """Run the install script on a host over its normal login (root, or sudo without a
        password). What it runs is exactly what the setup page shows."""
        require_signin(request)
        found = {r: (id, user) for r, id, user in roles()}
        if role not in found:
            raise HTTPException(404, "pki or server")
        ovpn = hub.config.vpn.openvpn
        assert ovpn is not None
        id, _ = found[role]
        device = hub.config.devices[id]
        if device.ssh is None:
            raise HTTPException(400, f"{id} has no ssh: login to install with; run the commands by hand")
        text = install_script(role, ovpn, hub.keys.public_key("vpn"), await hub_ip_for(device))
        target = Target(device.host, device.ssh.port, device.ssh.user)
        command = "sh -s" if device.ssh.user == "root" else "sudo -n sh -s"

        async def go() -> str:
            code, out, err = await run(target, "hub", command, text, timeout=300)
            if code != 0:
                raise RuntimeError(
                    (err or out).strip().splitlines()[-1] if (err or out).strip() else f"exit {code}"
                )
            return out

        output = await attempt(request, f"vpn.openvpn.install.{role}", go(), device=id)
        return {"output": output[-4000:]}

    return api


_private = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _download(text: str, filename: str) -> Response:
    """A config with a private key in it: a download, never cached."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "client.conf"
    headers = _private | {"Content-Disposition": f'attachment; filename="{safe}"'}
    return Response(text, media_type="application/octet-stream", headers=headers)
