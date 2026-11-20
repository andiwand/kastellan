"""Settings kept the same across a group of OpenWrt devices (`groups:` in config.yaml): Wi-Fi
networks (name, password, encryption), the mesh (its id and password), root's password.

Nothing is stored as "the wanted state": the devices are the truth. Kastellan reads every member,
lines their networks up by name (an access-point network called "home" on three devices is one
network), and shows where they differ; a change goes to every member, each with its own rollback.
Passwords are compared by a fingerprint and never shown.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from typing import Any

from . import client as ow


def _fp(secret: str | None, salt: bytes) -> str | None:
    """A fingerprint of a password, to see whether two devices have the same one; keyed with a
    secret of the hub's, so it can't be used to guess the password."""
    return hmac.new(salt, secret.encode(), hashlib.sha256).hexdigest()[:16] if secret else None


def _ifaces(values: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(n, s) for n, s in values.items() if s.get(".type") == "wifi-iface"]


async def survey(members: dict[str, ow.Ubus], salt: bytes) -> dict[str, Any]:
    """Every member's Wi-Fi networks and mesh, lined up; what differs."""

    async def one(id: str, ubus: ow.Ubus) -> tuple[str, dict | Exception]:
        try:
            return id, (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
        except Exception as e:
            return id, e

    got = dict(await asyncio.gather(*(one(i, u) for i, u in members.items())))
    networks: dict[str, dict[str, Any]] = {}
    mesh: dict[str, list[dict[str, Any]]] = {}
    errors = {i: str(v)[:200] for i, v in got.items() if isinstance(v, Exception)}
    for id, values in got.items():
        if isinstance(values, Exception):
            continue
        for section, sec in _ifaces(values):
            band = (values.get(sec.get("device")) or {}).get("band")
            entry = {
                "device": id,
                "section": section,
                "band": band,
                "encryption": sec.get("encryption"),
                "key": _fp(sec.get("key"), salt),
                "disabled": sec.get("disabled") == "1",
            }
            if sec.get("mode") == "mesh":
                mesh.setdefault(sec.get("mesh_id") or "", []).append(entry)
            elif sec.get("mode", "ap") == "ap" and sec.get("ssid"):
                networks.setdefault(sec["ssid"], {"ssid": sec["ssid"], "on": []})["on"].append(entry)
    for net in networks.values():
        on = net["on"]
        net["devices"] = sorted({e["device"] for e in on})
        net["missing"] = sorted(set(members) - set(errors) - set(net["devices"]))
        net["same_key"] = len({e["key"] for e in on}) == 1
        net["same_encryption"] = len({e["encryption"] for e in on}) == 1
    return {
        "members": sorted(members),
        "errors": errors,
        "networks": sorted(networks.values(), key=lambda n: (-len(n["devices"]), n["ssid"])),
        "mesh": [
            {
                "mesh_id": mid,
                "on": on,
                "devices": sorted({e["device"] for e in on}),
                "missing": sorted(set(members) - set(errors) - {e["device"] for e in on}),
                "same_key": len({e["key"] for e in on}) == 1,
            }
            for mid, on in sorted(mesh.items())
        ],
    }


async def change_network(
    members: dict[str, ow.Ubus],
    ssid: str,
    values: dict[str, str],
    mesh: bool = False,
    say=None,
) -> dict[str, str]:
    """Set name/password/encryption on every member's network called `ssid` (or the mesh with
    that id), one device after the other, each applied with its rollback. The outcome per device."""
    allowed = {"ssid", "key", "encryption"} if not mesh else {"mesh_id", "key", "encryption"}
    clean = {k: v for k, v in values.items() if k in allowed and v}
    if not clean:
        raise ValueError("nothing to change")
    outcome: dict[str, str] = {}
    for id, ubus in members.items():
        try:
            current = (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
            sections = [
                n
                for n, s in _ifaces(current)
                if (s.get("mode") == "mesh" and s.get("mesh_id") == ssid)
                or (not mesh and s.get("mode", "ap") == "ap" and s.get("ssid") == ssid)
                if (s.get("mode") == "mesh") == mesh
            ]
            if not sections:
                outcome[id] = "doesn't have it"
                continue
            await ow.change_wifi(ubus, {n: clean for n in sections})
            outcome[id] = "done"
        except Exception as e:
            outcome[id] = f"failed: {str(e)[:160] or type(e).__name__}"
        if say:
            say(f"{id}: {outcome[id]}")
    return outcome


async def set_root_password(members: dict[str, ow.Ubus], password: str, say=None) -> dict[str, str]:
    """root's password on every member (rpc-sys password_set; the `password` ability)."""
    if len(password) < 8 or any(ord(c) < 32 for c in password):
        raise ValueError("at least 8 characters, no control characters")
    outcome: dict[str, str] = {}
    for id, ubus in members.items():
        try:
            async with ubus.changing:
                await ubus.call("rpc-sys", "password_set", {"user": "root", "password": password})
            outcome[id] = "done"
        except Exception as e:
            outcome[id] = f"failed: {str(e)[:160] or type(e).__name__}"
        if say:
            say(f"{id}: {outcome[id]}")
    return outcome


async def can_mesh(ubus: ow.Ubus) -> bool | None:
    """Whether the device's Wi-Fi software can do 802.11s mesh with a password (the full
    wpad/wpa_supplicant, e.g. wpad-mesh-mbedtls; the default wpad-basic can't). As LuCI tells
    it (luci getFeatures); None: can't tell."""
    try:
        features = await ubus.call("luci", "getFeatures")
    except Exception:
        return None
    wpa = features.get("wpasupplicant") or {}
    return bool(wpa.get("mesh") and wpa.get("sae"))


async def add_mesh(ubus: ow.Ubus, radio: str, mesh_id: str, key: str, network: str = "lan") -> str:
    """A mesh network on one radio (SAE-encrypted, bridged into `network`), applied with the
    rollback. The new section's name."""
    if await can_mesh(ubus) is False:
        raise ValueError("its Wi-Fi software can't do mesh (install wpad-mesh-mbedtls or wpad-mbedtls first)")
    current = (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
    if (current.get(radio) or {}).get(".type") != "wifi-device":
        raise ValueError(f"no radio {radio}")
    if any(s.get("mode") == "mesh" and s.get("device") == radio for _, s in _ifaces(current)):
        raise ValueError(f"{radio} already has a mesh")
    return await ow.add_wifi_iface(
        ubus,
        {
            "device": radio,
            "mode": "mesh",
            "mesh_id": mesh_id,
            "encryption": "sae",
            "key": key,
            "network": network,
            "mesh_fwding": "1",
        },
    )
