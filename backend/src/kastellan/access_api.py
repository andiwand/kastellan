"""SSH access over HTTP: what works on a device, the commands to set it up, and setting it up
with a login and password typed once (access.py)."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from . import setup_run
from .access import USER, hub_address, setup_script
from .auth_api import client_address, require_admin, require_signin
from .setup_run import Login, reason
from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


class Setup(Login):
    user: str = Field(min_length=1, max_length=32)  # the account Kastellan will use
    create: bool = False  # create `user` (needs root)
    sudo: bool = False  # passwordless sudo for `user` (needs root)


def script_or_none(user: str, key: str, hub_ip: str | None, create: bool, sudo: bool) -> str | None:
    try:
        return setup_script(user, key, hub_ip, create, sudo)
    except ValueError:
        return None  # a login name the commands can't take; the page says so


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/devices")

    def device_of(id: str):
        device = hub.config.devices.get(id)
        if device is None:
            raise HTTPException(404, "no such device")
        return device

    @api.get("/{id}/access")
    async def access(id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        device = device_of(id)
        port = device.ssh.port if device.ssh else 22
        user = device.ssh.user if device.ssh else "kastellan"
        hub_ip = await asyncio.to_thread(hub_address, device.host, port)
        key = hub.keys.public_key()
        checks: list[dict[str, Any]] = []

        def note(name: str, ok: bool, detail: str = "") -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        try:
            await hub.keys.check_host(Target(device.host, port, user))
            note("Host key trusted", True)
            if device.ssh:
                try:
                    async with await hub.keys.connect(Target(device.host, port, user)) as conn:
                        r = await conn.run(
                            "sudo -n true 2>/dev/null && echo sudo; id -u",
                            check=False,
                            stdin=asyncssh.DEVNULL,
                            timeout=20,
                        )
                    out = str(r.stdout or "").split()
                    note(f"Kastellan logs in as {user}", True)
                    note(
                        "Can install (root or sudo without a password)",
                        "sudo" in out or out[-1:] == ["0"],
                        "" if ("sudo" in out or out[-1:] == ["0"]) else "fine for watching; installs need it",
                    )
                except asyncssh.PermissionDenied:
                    note(f"Kastellan logs in as {user}", False, "the key isn't in its authorized_keys yet")
            else:
                note("A login in config.yaml", False, "add ssh: to the device once access is set up")
        except UnknownHost as e:
            note("Host key trusted", False, f"trust {e.fingerprint} on the terminal page first")
        except ChangedHost as e:
            note("Host key trusted", False, str(e))
        except Exception as e:
            note("Host key trusted", False, f"can't reach {device.host}: {reason(e)}")
        return {
            "user": user,
            "port": port,
            "hub_ip": hub_ip,
            "key": key,
            "checks": checks,
            "scripts": {
                # One login for everything: Kastellan's key for an existing user.
                "existing": script_or_none(user, key, hub_ip, create=False, sudo=False),
                # A user of its own, with sudo for installs (run as root).
                "dedicated": setup_script("kastellan", key, hub_ip, create=True, sudo=True),
            },
        }

    @api.post("/{id}/access/setup")
    async def set_up(id: str, body: Setup, request: Request) -> StreamingResponse:
        """Log in once with the typed password and run the commands the page shows (as root
        when they need it), streaming the output; then `ssh:` goes into config.yaml."""
        w = require_signin(request)
        device = device_of(id)
        if not (USER.match(body.login) and USER.match(body.user)):
            raise HTTPException(400, "user names are letters, digits, . _ - (up to 32, not starting with -)")
        hub_ip = await asyncio.to_thread(hub_address, device.host, body.port)
        script = setup_script(body.user, hub.keys.public_key(), hub_ip, body.create, body.sudo)
        if not (body.create or body.sudo or body.user != body.login):
            body.elevate = "none"  # its own key: as itself
        details = {
            "device": id,
            "login": body.login,
            "account": body.user,  # not "user": that is who did it (Audit.record)
            "create": body.create,
            "sudo": body.sudo,
            "elevate": body.elevate,
        }
        audit = partial(hub.audit.record, "access.setup", w.user, client_address(request.scope), **details)
        conn = await setup_run.connect(hub, device.host, body, audit)

        def after() -> dict[str, Any]:
            # Kastellan logs in as this user from now on: ssh: in config.yaml says so (replacing
            # another user there: that's what setting it up for this one means).
            ssh = {"user": body.user} | ({"port": body.port} if body.port != 22 else {})
            wrote = setup_run.add_device_key(
                hub, w.user, client_address(request.scope), id, "ssh", body.port, ssh, replace=True
            )
            text = "ssh:\n  " + "\n  ".join(f"{k}: {v}" for k, v in ssh.items())
            return {"config": text} if wrote else {}

        return setup_run.stream(conn, script, body, audit, after)

    return api
