"""Which of Kastellan's secrets is used for what: the logins it keeps for devices (OpenWrt,
FRITZ!Box), service accounts (wg-easy), credentials in notification channels (`secret:<name>`
references), and the keys it made for itself. The Keys page lists them with where each is
used, and references to secrets that aren't set.

Only names and places: never a value."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request

from .auth_api import require_admin
from .secrets import OWN
from .vault import KASTELLAN

if TYPE_CHECKING:
    from .hub import Hub


def usage(hub: Hub) -> dict[str, list[str]]:
    """secret name -> where it is used (readable places, config.yaml's words)."""
    out: dict[str, list[str]] = {}

    def use(name: str | None, where: str) -> None:
        if name:
            out.setdefault(name, []).append(where)

    for id, d in hub.config.devices.items():
        if d.openwrt is not None:
            use(hub.openwrt_secret(id), f"{id}: Kastellan's OpenWrt login")
        if d.fritzbox is not None:
            use(hub.fritz_secret(id), f"{id}: Kastellan's FRITZ!Box user")
    for vid, v in hub.config.vpns.items():
        if v.kind == "wireguard":
            use(v.password_secret, f"VPN {v.name or vid}: wg-easy's login")
    for i, c in enumerate(hub.config.notify):
        for field, name in c.secret_refs().items():
            use(name, f"notify #{i + 1} ({c.kind}): {field}")
    for name, what in OWN.items():
        if hub.secrets.has(name):
            use(name, what)
    return out


def view(hub: Hub) -> list[dict[str, Any]]:
    used = usage(hub)
    names = set(used)
    if hub.secrets.in_vault and hub.vault.exists:
        names |= {e.id for e in hub.vault.entries() if e.ring == KASTELLAN and e.kind != "ssh-key"}
    else:
        names |= set(hub.secrets.names())
    rows = []
    for name in sorted(names):
        is_set = hub.secrets.has(name)
        rows.append({"name": name, "set": is_set, "used_by": used.get(name, []), "missing": not is_set})
    return rows


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/secrets")

    @api.get("")
    async def secrets(request: Request) -> list[dict[str, Any]]:
        """Kastellan's secrets by name: set or not, and what uses each (admins; no values)."""
        require_admin(request)
        return [] if hub.demo else view(hub)

    return api
