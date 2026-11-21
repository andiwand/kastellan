"""Devices on the network that Kastellan doesn't know yet: from the routers' client lists (a
FRITZ!Box's hosts, OpenWrt's DHCP leases and Wi-Fi clients). The Devices page suggests them;
nothing is added without an admin's say, and one can be set aside for good ("not a device to
watch": a phone, a guest).

Known means: a configured device's host is the address (or resolves to it), or its name."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from .auth_api import client_address, require_admin

if TYPE_CHECKING:
    from .hub import Hub


class Ignored:
    """MAC addresses set aside (`<data>/discovery-ignored.json`)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def get(self) -> set[str]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return set()
        return {m for m in data if isinstance(m, str)} if isinstance(data, list) else set()

    def add(self, mac: str) -> None:
        with self._lock:
            macs = sorted(self.get() | {mac})[-5000:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(macs))
            tmp.replace(self.path)


def found(hub: Hub) -> list[dict[str, Any]]:
    """Clients that match no configured device, sorted by name and MAC address."""
    known_hosts: set[str] = set()
    known_names: set[str] = set()
    for id, d in hub.config.devices.items():
        known_names |= {id.lower(), d.name.lower()} if d.name else {id.lower()}
        if d.host:
            known_hosts.add(d.host.lower())
            if address := hub.resolver.address(d.host):
                known_hosts.add(address)
    ignored = hub.discovery_ignored.get()
    by_mac: dict[str, dict[str, Any]] = {}

    def add(mac: Any, name: Any, address: Any, via: str, router: str, wifi: bool = False) -> None:
        mac = str(mac or "").lower()
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac) or mac in ignored:
            return
        entry = by_mac.setdefault(
            mac, {"mac": mac, "name": None, "address": None, "routers": [], "wifi": False, "via": None}
        )
        entry["name"] = entry["name"] or (str(name)[:64] if name else None)
        entry["address"] = entry["address"] or (str(address) if address else None)
        entry["wifi"] = entry["wifi"] or wifi
        entry["via"] = entry["via"] or via
        if router not in entry["routers"]:
            entry["routers"].append(router)

    for rid, status in hub.monitor.status.items():
        if rid not in hub.config.devices or status is None:
            continue
        fritz = status.fritz or {}
        for c in fritz.get("clients") or []:
            add(
                c.get("mac"),
                c.get("name"),
                c.get("address"),
                c.get("via") or "",
                rid,
                c.get("via") == "802.11",
            )
        router = status.router or {}
        for lease in (router.get("dhcp") or {}).get("leases") or []:
            add(lease.get("mac"), lease.get("name"), lease.get("address"), "DHCP", rid)
        for radio in router.get("radios") or []:
            for iface in radio.get("interfaces") or []:
                for c in iface.get("clients") or []:
                    add(c.get("mac"), c.get("name"), None, "Wi-Fi", rid, True)
    out = []
    for e in by_mac.values():
        if (e["address"] and e["address"].lower() in known_hosts) or (
            e["name"] and e["name"].lower() in known_names | known_hosts
        ):
            continue
        out.append(e)
    return sorted(out, key=lambda e: ((e["name"] or "~").lower(), e["mac"]))


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/discovered")

    @api.get("")
    async def view(request: Request) -> list[dict[str, Any]]:
        """Admins: devices the routers see that Kastellan doesn't know."""
        require_admin(request)
        return found(hub)

    @api.post("/{mac}/ignore")
    async def ignore(mac: str, request: Request) -> dict[str, Any]:
        w = require_admin(request)
        mac = mac.lower()
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac):
            raise HTTPException(400, "not a MAC address")
        hub.discovery_ignored.add(mac)
        hub.audit.record("discovery.ignore", w.user, client_address(request.scope), mac=mac)
        return {"ok": True}

    return api
