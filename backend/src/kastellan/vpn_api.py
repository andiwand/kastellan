"""VPN access over HTTP: who has access to each VPN (`vpns:`, any number of either kind:
WireGuard through wg-easy, OpenVPN through easy-rsa), giving it, taking it away, and the client's
config to download. Adding, changing and removing VPNs is config_api's (it writes config.yaml).

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
from .vpn.rotation import VpnRotator
from .vpn.wireguard import WgEasy

if TYPE_CHECKING:
    from .config import Device
    from .hub import Hub

log = logging.getLogger(__name__)


class Finish(BaseModel):
    anyway: bool = False  # switch even if the new key isn't on every host yet


class Forget(BaseModel):
    host: str = Field(max_length=40)  # a role of the rotation: pki, server


class NewClient(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    days: int | None = Field(default=None, ge=1, le=3650)  # None: wg-easy never; OpenVPN: the default


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")
    rotator = VpnRotator(hub.keys)
    # A rotation started before several VPNs named its hosts "pki" and "server".
    rotator.rename_hosts({"pki": "openvpn:pki", "server": "openvpn:server"})

    def vpn_of(vid: str, kind: str) -> Any:
        vpn = hub.config.vpns.get(vid)
        if vpn is None or vpn.kind != kind:
            raise HTTPException(404, f"no {kind} VPN {vid!r} in config.yaml")
        return vpn

    def wireguard(vid: str) -> WgEasy:
        wg = vpn_of(vid, "wireguard")
        try:
            password = hub.secrets.get(wg.password_secret)
        except KeyError as e:
            raise HTTPException(409, "wg-easy's password isn't set: enter it in this VPN's Settings") from e
        return WgEasy(wg.wg_easy, wg.user, password)

    def openvpn(vid: str) -> OpenVpn:
        vpn_of(vid, "openvpn")
        return hub.openvpn(vid)

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

    @api.get("/vpn")
    async def overview(request: Request) -> dict[str, Any]:
        """Every VPN with its clients (in the order config.yaml has them)."""
        # Error details (hosts, what failed) are for admins; others learn only that it failed.
        admin = who(request).is_admin

        def say(e: Exception, what: str) -> str:
            return str(problem(e).detail) if admin else f"{what} can't be reached right now"

        async def one(vid: str, vpn: Any) -> dict[str, Any]:
            out: dict[str, Any] = {
                "id": vid,
                "kind": vpn.kind,
                "name": hub.config.vpn_name(vid),
                "clients": [],
            }
            if admin:  # what its settings form starts from (secrets are names only)
                out["settings"] = vpn.model_dump(mode="json")
                if vpn.kind == "wireguard":
                    out["password_set"] = has_secret(vpn.password_secret)
            if vpn.kind == "wireguard":
                url = httpx.URL(vpn.wg_easy)
                # Basic auth over plain HTTP to another machine: the password crosses the network.
                out["insecure"] = url.scheme == "http" and url.host not in ("localhost", "127.0.0.1", "::1")
                out["where"] = vpn.wg_easy if admin else None
                try:
                    out["clients"] = [asdict(c) for c in await wireguard(vid).clients()]
                except Exception as e:
                    out["error"] = say(e, "wg-easy")
                return out
            out |= {
                "mode": vpn.mode,
                "crl_to_server": vpn.server is not None,
                "crl": hub.crl_state.get(vid, {}),
            }
            out["where"] = {"pki": vpn.pki, "server": vpn.server} if admin else None
            try:
                out["clients"] = [asdict(c) for c in await openvpn(vid).clients()]
            except Exception as e:
                out["error"] = say(e, "The PKI host")
            out["seen"] = seen_view(vid)
            connected = {c["name"]: c for c in fresh_connections(vid)}
            last = hub.seen(vid).last
            for c in out["clients"]:
                c["connected"] = connected.get(c["name"])
                c["last_seen"] = last.get(c["name"])
            return out

        vpns = await asyncio.gather(*(one(vid, v) for vid, v in hub.config.vpns.items()))
        return {"vpns": list(vpns)}

    def has_secret(name: str) -> bool:
        try:
            hub.secrets.get(name)
        except Exception:  # missing, locked or not Kastellan's to use: not usable now
            return False
        return True

    def fresh_connections(vid: str) -> list[dict[str, Any]]:
        """Who the router said is connected, if it said so lately (else nobody is known to be)."""
        from .hub import ROUTER_EVERY  # (hub imports this module)

        status = hub.ovpn_status.get(vid, {})
        checked = status.get("checked")
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(checked)).total_seconds() if checked else None
        except ValueError:
            age = None
        if age is None or age > 3 * ROUTER_EVERY or status.get("error"):
            return []
        return status.get("connected") or []

    def seen_view(vid: str) -> dict[str, Any]:
        """Whether Kastellan can see who is connected to the OpenVPN server, and if not, why."""
        ovpn = hub.config.vpns[vid]
        server = hub.config.devices.get(ovpn.server) if ovpn.server else None
        if server is None or server.openwrt is None:
            return {"how": "no-openwrt", "checked": None, "error": None}
        status = hub.ovpn_status.get(vid, {})
        error = status.get("error")
        return {
            "how": "not-allowed" if error and "not allowed" in error else "openwrt",
            "device": ovpn.server,
            "checked": status.get("checked"),
            "error": error,
        }

    # --- WireGuard -------------------------------------------------------------------------------

    @api.post("/vpns/{vid}/wireguard")
    async def wg_create(vid: str, body: NewClient, request: Request) -> dict[str, str]:
        require_signin(request)
        expires = datetime.now(UTC) + timedelta(days=body.days) if body.days else None
        id = await attempt(
            request,
            "vpn.wireguard.create",
            wireguard(vid).create(body.name, expires),
            vpn=vid,
            client=body.name,
            days=body.days,
        )
        return {"id": id}

    @api.post("/vpns/{vid}/wireguard/{id}/{action}")
    async def wg_toggle(vid: str, id: str, action: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        if action not in ("enable", "disable"):
            raise HTTPException(404, "enable or disable")
        await attempt(
            request, f"vpn.wireguard.{action}", wireguard(vid).enable(id, action == "enable"), vpn=vid, id=id
        )
        return {"enabled": action == "enable"}

    @api.delete("/vpns/{vid}/wireguard/{id}")
    async def wg_delete(vid: str, id: str, request: Request) -> dict[str, bool]:
        require_signin(request)
        await attempt(request, "vpn.wireguard.delete", wireguard(vid).delete(id), vpn=vid, id=id)
        return {"deleted": True}

    @api.get("/vpns/{vid}/wireguard/{id}/config")
    async def wg_config(vid: str, id: str, request: Request) -> Response:
        require_signin(request)
        text = await attempt(
            request, "vpn.wireguard.download", wireguard(vid).configuration(id), vpn=vid, id=id
        )
        return _download(text, f"{vid}-{id}.conf")

    @api.get("/vpns/{vid}/wireguard/{id}/qr")
    async def wg_qr(vid: str, id: str, request: Request) -> Response:
        require_signin(request)
        svg = await attempt(request, "vpn.wireguard.qr", wireguard(vid).qrcode(id), vpn=vid, id=id)
        # An SVG from wg-easy is shown as an image only, never as a page (no script runs).
        return Response(
            svg,
            media_type="image/svg+xml",
            headers=_private | {"Content-Security-Policy": "script-src 'none'"},
        )

    # --- OpenVPN ---------------------------------------------------------------------------------

    @api.post("/vpns/{vid}/openvpn")
    async def ovpn_issue(vid: str, body: NewClient, request: Request) -> dict[str, str]:
        require_signin(request)
        if not NAME.match(body.name):
            raise HTTPException(400, "names are lowercase letters, digits, . _ - (up to 64)")
        days = body.days or vpn_of(vid, "openvpn").days
        await attempt(
            request,
            "vpn.openvpn.issue",
            openvpn(vid).issue(body.name, days),
            vpn=vid,
            client=body.name,
            days=days,
        )
        return {"name": body.name}

    @api.post("/vpns/{vid}/openvpn/{name}/revoke")
    async def ovpn_revoke(vid: str, name: str, request: Request) -> dict[str, Any]:
        require_signin(request)
        await attempt(request, "vpn.openvpn.revoke", openvpn(vid).revoke(name), vpn=vid, client=name)
        # The revoke stands (and is in the audit log) even if the server can't be reached now;
        # the page then offers to push the CRL again, and the daily renewal does too.
        try:
            await attempt(request, "vpn.openvpn.crl", hub.push_crl(vid), vpn=vid, after=f"revoking {name}")
        except HTTPException as e:
            return {"revoked": True, "crl_pushed": False, "error": e.detail}
        return {"revoked": True, "crl_pushed": True}

    @api.post("/vpns/{vid}/openvpn/crl")
    async def ovpn_push_crl(vid: str, request: Request) -> dict[str, Any]:
        require_signin(request)
        vpn_of(vid, "openvpn")
        await attempt(request, "vpn.openvpn.crl", hub.push_crl(vid, renew=True), vpn=vid)
        return {"crl": hub.crl_state.get(vid, {})}

    @api.get("/vpns/{vid}/openvpn/{name}/config")
    async def ovpn_config(vid: str, name: str, request: Request) -> Response:
        require_signin(request)
        text = await attempt(request, "vpn.openvpn.download", openvpn(vid).export(name), vpn=vid, client=name)
        return _download(text, f"{name}.ovpn")

    # --- Rotating the VPN key (restricted mode) -------------------------------------------------

    def key_hosts(needed: bool = True) -> dict[str, Target]:
        """The restricted logins the VPN key is bound to, over every OpenVPN VPN in restricted
        mode: "<vpn>:<role>" -> where. Without `needed` (finishing, cancelling, forgetting a
        rotation started earlier), none is an empty map: its hosts are then left to forget."""
        restricted = [v for v, s in hub.config.vpns.items() if s.kind == "openvpn" and s.mode == "restricted"]
        if not restricted and needed:
            raise HTTPException(
                409, "no OpenVPN in restricted mode: the VPN key isn't used (direct mode uses the hub's key)"
            )
        out = {}
        for vid in restricted:
            for role, id, user in roles(vid):
                device = hub.config.devices[id]
                out[f"{vid}:{role}"] = Target(device.host, device.ssh.port if device.ssh else 22, user)
        return out

    def key_view() -> dict[str, Any]:
        rot = rotator.state()
        key = hub.keys.hub_key("vpn")
        nxt = hub.keys.side_key("next", "vpn")
        return {
            "fingerprint": key.get_fingerprint(),
            "next": nxt.get_fingerprint() if nxt else None,
            "rotation": None if rot is None else asdict(rot),
        }

    @api.get("/vpn/key")
    def vpn_key(request: Request) -> dict[str, Any]:
        require_admin(request)
        return key_view()

    async def rotate(request: Request, what: str, work: Any) -> dict[str, Any]:
        """One step of the rotation (work: hosts -> awaitable), audited."""
        w = require_signin(request)
        try:
            rot = await work(key_hosts(needed=what == "start"))
        except (ValueError, FileNotFoundError) as e:
            hub.audit.record(f"vpn.key.rotate.{what}", w.user, client_address(request.scope), outcome=str(e))
            raise HTTPException(409, str(e)) from e
        steps = {
            r: "; ".join(
                x
                for x in (
                    "removed the old key" if s.removed else "has the new key" if s.added else None,
                    s.note,
                    s.error,
                )
                if x
            )
            for r, s in (rot.devices.items() if rot else [])
        }
        hub.audit.record(
            f"vpn.key.rotate.{what}",
            w.user,
            client_address(request.scope),
            stage=rot.stage if rot else None,
            hosts=steps,
        )
        return key_view()

    @api.post("/vpn/key/rotate/start")
    async def rotate_start(request: Request) -> dict[str, Any]:
        return await rotate(request, "start", rotator.start)

    @api.post("/vpn/key/rotate/finish")
    async def rotate_finish(body: Finish, request: Request) -> dict[str, Any]:
        return await rotate(request, "finish", lambda hosts: rotator.finish(hosts, anyway=body.anyway))

    @api.post("/vpn/key/rotate/cancel")
    async def rotate_cancel(request: Request) -> dict[str, Any]:
        return await rotate(request, "cancel", rotator.cancel)

    @api.post("/vpn/key/rotate/forget")
    async def rotate_forget(body: Forget, request: Request) -> dict[str, Any]:
        """Give up on one host while removing (it can't be reached any more): the rotation ends
        without it, and the old key may stay trusted there; audited as such."""
        return await rotate(request, "forget", lambda hosts: rotator.forget(body.host))

    # --- Setting up OpenVPN on the hosts --------------------------------------------------------

    def roles(vid: str) -> list[tuple[str, str, str]]:
        """(role, device id, the restricted login) for a VPN's PKI host and server."""
        ovpn = vpn_of(vid, "openvpn")
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

    async def checks(vid: str, role: str, id: str, user: str) -> list[dict[str, Any]]:
        """What works now on one host, tried for real."""
        ovpn = vpn_of(vid, "openvpn")
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

    @api.get("/vpns/{vid}/openvpn/setup")
    async def setup(vid: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        found_roles = roles(vid)  # 404 for no such OpenVPN
        ovpn = vpn_of(vid, "openvpn")
        key = hub.keys.public_key("vpn")
        hosts = []
        for role, id, user in found_roles:
            device = hub.config.devices[id]
            found = await checks(vid, role, id, user)
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
                    "script": install_script(role, ovpn, key, await hub_ip_for(device), vid),
                }
            )
        return {"mode": ovpn.mode, "vpn_key": key, "hosts": hosts}

    @api.post("/vpns/{vid}/openvpn/setup/{role}/install")
    async def install(vid: str, role: str, request: Request) -> dict[str, Any]:
        """Run the install script on a host over its normal login (root, or sudo without a
        password). What it runs is exactly what the setup page shows."""
        require_signin(request)
        found = {r: (id, user) for r, id, user in roles(vid)}
        if role not in found:
            raise HTTPException(404, "pki or server")
        ovpn = vpn_of(vid, "openvpn")
        id, _ = found[role]
        device = hub.config.devices[id]
        if device.ssh is None:
            raise HTTPException(400, f"{id} has no ssh: login to install with; run the commands by hand")
        text = install_script(role, ovpn, hub.keys.public_key("vpn"), await hub_ip_for(device), vid)
        target = Target(device.host, device.ssh.port, device.ssh.user)
        command = "sh -s" if device.ssh.user == "root" else "sudo -n sh -s"

        async def go() -> str:
            code, out, err = await run(target, "hub", command, text, timeout=300)
            if code != 0:
                raise RuntimeError(
                    (err or out).strip().splitlines()[-1] if (err or out).strip() else f"exit {code}"
                )
            return out

        output = await attempt(request, f"vpn.openvpn.install.{role}", go(), vpn=vid, device=id)
        return {"output": output[-4000:]}

    return api


_private = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _download(text: str, filename: str) -> Response:
    """A config with a private key in it: a download, never cached."""
    safe = "".join(ch for ch in filename if ch.isalnum() or ch in "._-") or "client.conf"
    headers = _private | {"Content-Disposition": f'attachment; filename="{safe}"'}
    return Response(text, media_type="application/octet-stream", headers=headers)
