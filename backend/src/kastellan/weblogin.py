"""Signing in to a device's web page for the admin, without the password reaching the browser.

The admin picks a login stored for the device (People key ring) and types their own password
again (as for revealing it). The hub reads the login from the ring and signs in to the device
itself, through the same address the device page goes to:
- OpenWrt's LuCI (its login form): the device's session cookie goes to the browser, on the
  device's own name, with the page; the password stays on the hub;
- HTTP Basic (a page that asks with 401 and `WWW-Authenticate: Basic`): the credentials stay on
  the hub, which adds them to this page's requests (webproxy.Access);
- anything else: not done here ("Copy password" stays the way).

What it hands back is a one-time ticket to the device's name, as "Open web interface" does.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import SESSION_COOKIE
from .auth_api import MAX_PASSWORD, client_address, confirm_password, require_signin
from .setup_run import stored_logins
from .webproxy import ENTER, Access, host_only, label, new_client, page_origin, upstream

if TYPE_CHECKING:
    from .hub import Hub


@dataclass
class Session:
    """What a sign-in got: cookies for the browser (the device's own), or an Authorization
    header the hub adds to the page's requests."""

    how: str  # luci, basic
    cookies: list[str] = field(default_factory=list)
    basic: str | None = None


class SignIn(BaseModel):
    entry: str = Field(min_length=1, max_length=200)  # the stored login's id
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)  # the admin's own, again


async def sign_in(client: httpx.AsyncClient, target: str, user: str, password: str) -> Session:
    """Sign in to the device at `target` (its base URL); ValueError says why not."""
    # LuCI (OpenWrt): its form at /cgi-bin/luci, a sysauth cookie on success.
    probe = await client.get(f"{target}/cgi-bin/luci/", follow_redirects=False)
    if probe.status_code in (200, 403) and "luci_password" in probe.text:
        r = await client.post(
            f"{target}/cgi-bin/luci/",
            data={"luci_username": user, "luci_password": password},
            follow_redirects=False,
        )
        cookies = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie" and "sysauth" in v]
        if r.status_code in (302, 303) and cookies:
            return Session("luci", cookies=cookies)
        raise ValueError("the device refused the login (LuCI)")
    # HTTP Basic: the page asks for it.
    first = await client.get(f"{target}/", follow_redirects=False)
    challenge = first.headers.get("www-authenticate", "")
    if first.status_code == 401 and challenge.lower().startswith("basic"):
        header = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        again = await client.get(f"{target}/", headers={"Authorization": header}, follow_redirects=False)
        if again.status_code >= 400:
            raise ValueError("the device refused the login (HTTP Basic)")
        return Session("basic", basic=header)
    raise ValueError("this device's login page isn't one Kastellan can fill in: copy the password instead")


def router(hub: Hub, access: Access) -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.post("/devices/{id}/web/sign-in")
    async def web_sign_in(id: str, body: SignIn, request: Request) -> dict[str, str]:
        """Sign in to the device's page with a stored login; a link to open it, signed in."""
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(403, "not in demo mode")
        web, device = hub.config.web, hub.config.devices.get(id)
        target = upstream(device) if device else None
        if web is None or device is None or target is None:
            raise HTTPException(404, "no web interface for that device through Kastellan")
        await confirm_password(hub, w, body.password)
        # The logins a setup or terminal may use here (setup_run); and, as for revealing one,
        # the password typed again must open the ring itself.
        entry = hub.vault.entry(body.entry)
        if entry is None or not any(e["id"] == entry.id for e in stored_logins(hub, id, w.user)["stored"]):
            raise HTTPException(404, "no login stored for this device by that id")
        if not await asyncio.to_thread(hub.vault.opens_with, entry.ring, w.user or "", body.password):
            raise HTTPException(403, "your password doesn't open this ring (sign in again to get a slot)")
        secret = hub.vault.get(entry.id, by=w.user)
        address = client_address(request.scope)
        try:
            async with new_client() as client:
                session = await sign_in(client, target, entry.user, secret)
        except ValueError as e:
            hub.audit.record("web.sign-in", w.user, address, device=id, entry=entry.id, outcome=str(e)[:120])
            raise HTTPException(409, str(e)) from e
        except httpx.HTTPError as e:
            hub.audit.record("web.sign-in", w.user, address, device=id, entry=entry.id, outcome="unreachable")
            raise HTTPException(502, f"the device doesn't answer: {type(e).__name__}") from e
        finally:
            del secret
        headers = {k.lower(): v for k, v in request.headers.items()}
        where = page_origin(request.scope, headers, id, web.domain)
        insecure = where.startswith("http:")
        key = access.ticket(
            request.cookies.get(SESSION_COOKIE) or "",
            label(id),
            target,
            cookies=[c for c in (host_only(v, insecure) for v in session.cookies) if c],
            basic=session.basic,
        )
        hub.audit.record(
            "web.sign-in", w.user, address, device=id, entry=entry.id, how=session.how, outcome="done"
        )
        return {"url": f"{where}{ENTER}?ticket={key}", "how": session.how}

    return api
