"""SSH access over HTTP: what works on a device, the commands to set it up, and setting it up
with a login and password typed once (access.py)."""

from __future__ import annotations

import asyncio
import shlex
from functools import partial
from typing import TYPE_CHECKING, Any

import asyncssh
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .access import USER, hub_address, setup_script
from .auth_api import client_address, require_admin, require_signin
from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub


class Setup(BaseModel):
    login: str = Field(min_length=1, max_length=32)  # an existing account on the device
    password: str = Field(min_length=1, max_length=1024)  # for this one connection, never kept
    user: str = Field(min_length=1, max_length=32)  # the account Kastellan will use
    create: bool = False  # create `user` (needs root or sudo)
    sudo: bool = False  # passwordless sudo for `user` (needs root or sudo)
    port: int = Field(default=22, ge=1, le=65535)


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
            note("Host key trusted", False, f"can't reach {device.host}: {type(e).__name__}")
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
    async def set_up(id: str, body: Setup, request: Request) -> dict[str, Any]:
        """Log in once with the typed password and run the commands the page shows."""
        w = require_signin(request)
        device = device_of(id)
        if not (USER.match(body.login) and USER.match(body.user)):
            raise HTTPException(400, "user names are letters, digits, . _ - (up to 32, not starting with -)")
        hub_ip = await asyncio.to_thread(hub_address, device.host, body.port)
        script = setup_script(body.user, hub.keys.public_key(), hub_ip, body.create, body.sudo)
        privileged = body.create or body.sudo or (body.user != body.login)
        target = Target(device.host, body.port, body.login)
        details = {
            "device": id,
            "login": body.login,
            "account": body.user,  # not "user": that is who did it (Audit.record)
            "create": body.create,
            "sudo": body.sudo,
        }
        audit = partial(hub.audit.record, "access.setup", w.user, client_address(request.scope), **details)
        try:
            async with await hub.keys.connect(target, password=body.password) as conn:
                if privileged and body.login != "root":
                    # sudo reads the same password from stdin; the script is its argument.
                    r = await conn.run(
                        f"sudo -S -p '' sh -c {shlex.quote(script)}",
                        input=body.password + "\n",
                        check=False,
                        timeout=60,
                    )
                else:
                    r = await conn.run("sh -s", input=script, check=False, timeout=60)
        except (UnknownHost, ChangedHost) as e:
            audit(outcome="host key")
            raise HTTPException(409, f"{e} (trust the host key first)") from e
        except asyncssh.PermissionDenied as e:
            audit(outcome="login refused")
            raise HTTPException(401, f"{body.login} with that password was refused") from e
        except (TimeoutError, asyncssh.TimeoutError) as e:
            audit(outcome="timed out")
            raise HTTPException(504, "timed out: some of it may have been done; check the device") from e
        except (OSError, asyncssh.Error) as e:
            audit(outcome="unreachable")
            raise HTTPException(502, f"can't reach {device.host}: {type(e).__name__}") from e
        except Exception as e:
            audit(outcome=f"failed: {type(e).__name__}")
            raise HTTPException(500, f"failed: {type(e).__name__}") from e
        output = (str(r.stdout or "") + str(r.stderr or "")).strip()
        ok = r.exit_status == 0
        audit(outcome="done" if ok else f"failed: {output[-200:]}")
        if not ok:
            raise HTTPException(502, output[-500:] or f"exit {r.exit_status}")
        return {
            "output": output[-2000:],
            "config": f"ssh:\n  user: {body.user}" + (f"\n  port: {body.port}" if body.port != 22 else ""),
        }

    return api
