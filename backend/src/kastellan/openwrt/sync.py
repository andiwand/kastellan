"""Settings kept the same across a group of OpenWrt devices (`groups:` in config.yaml): Wi-Fi
networks (name, password, encryption), the mesh (its id and password), root's password.

Nothing is stored as "the wanted state": the devices are the truth. Kastellan reads every member,
lines their networks up by name (an access-point network called "home" on three devices is one
network), and shows where they differ; a change goes to every member, each with its own rollback.
Passwords are compared by a fingerprint and never shown.
"""

from __future__ import annotations

import asyncio
import contextlib
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


async def survey(members: dict[str, ow.Ubus | Exception], salt: bytes) -> dict[str, Any]:
    """Every member's Wi-Fi networks and mesh, lined up; what differs."""

    async def one(id: str, ubus: ow.Ubus | Exception) -> tuple[str, dict | Exception, bool | None]:
        if isinstance(ubus, Exception):  # e.g. its login isn't set up yet
            return id, ubus, None
        try:
            values = (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
        except Exception as e:
            return id, e, None
        return id, values, await can_mesh(ubus)

    rows = await asyncio.gather(*(one(i, u) for i, u in members.items()))
    got = {id: values for id, values, _ in rows}
    mesh_ok = {id: ok for id, _, ok in rows}
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
                "radio": sec.get("device"),
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
    radios = {
        id: [
            {"section": n, "band": sec.get("band"), "channel": sec.get("channel")}
            for n, sec in values.items()
            if sec.get(".type") == "wifi-device"
        ]
        for id, values in got.items()
        if not isinstance(values, Exception)
    }
    return {
        "members": sorted(members),
        "errors": errors,
        "radios": radios,
        "can_mesh": mesh_ok,
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
    say=None,
) -> dict[str, str]:
    """Set name/password/encryption on every member's access point network called `ssid`, one
    device after the other, each applied with its rollback. The outcome per device. (Not the
    mesh: changed device by device it would cut members off; that is set_mesh.)"""
    clean = {k: v for k, v in values.items() if k in ("ssid", "key", "encryption") and v}
    if not clean:
        raise ValueError("nothing to change")
    outcome: dict[str, str] = {}
    for id, ubus in members.items():
        try:
            current = (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
            sections = [
                n for n, s in _ifaces(current) if s.get("mode", "ap") == "ap" and s.get("ssid") == ssid
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
            outcome[id] = "sent (the device doesn't report whether passwd took it)"
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
    if "mesh" not in wpa:  # LuCI before 24.10 doesn't say
        return None
    return bool(wpa.get("mesh") and wpa.get("sae"))


DFS = range(52, 145)  # 5 GHz channels a radio must listen on first (and may be moved off)


async def _staged_ok(ubus: ow.Ubus, session: str, before: dict[str, bool], sections: set[str]) -> bool:
    """After applying: the radios that were up are up, and these sections have an interface."""
    status = await ubus.call("network.wireless", "status", session=session)
    for name, radio in status.items():
        if radio.get("disabled"):
            continue
        if before.get(name, True) and (not radio.get("up") or radio.get("retry_setup_failed")):
            return False
    running = {
        i.get("section") for r in status.values() for i in r.get("interfaces") or [] if i.get("ifname")
    }
    return sections <= running


async def set_mesh(
    members: dict[str, ow.Ubus],
    mesh_id: str,
    key: str,
    radios: dict[str, str],
    channel: str | None = None,
    say=None,
    wait: float = 5,
    deadline: float = 100,
) -> dict[str, str]:
    """One mesh across the group: on a member that has a mesh (whatever its id), that mesh gets
    this id and password; on one without, a mesh is added on the radio named in `radios`. All mesh
    radios get one band and one fixed channel (802.11s needs that).

    All at once: some members may be reached only through the mesh, so changing them one by one
    would cut them off. Every member applies the change with its rollback at the same time, and it
    is confirmed only when every one of them answers again with its radios and mesh up; otherwise
    all of them undo it. The outcome per device."""
    ow.check_value("mesh_id", mesh_id)
    ow.check_value("key", key)
    if channel is not None:
        ow.check_value("channel", channel)
        if channel == "auto":
            raise ValueError("a mesh needs a fixed channel")
    note = (lambda line: say(line)) if say else (lambda line: None)
    outcome: dict[str, str] = {}
    async with contextlib.AsyncExitStack() as stack:
        for id in sorted(members):
            await stack.enter_async_context(members[id].changing)
        # The plan: what each member gets, read in its own session.
        plan: dict[str, dict[str, Any]] = {}
        for id, ubus in members.items():
            try:
                s = await ubus.session_id(min_left=250)
                current = (await ubus.call("uci", "get", {"config": "wireless"}, session=s)).get("values", {})
                have = [n for n, sec in _ifaces(current) if sec.get("mode") == "mesh"]
                if have:
                    mesh_radios = {current[n].get("device") for n in have}
                    plan[id] = {"set": have, "add": None}
                elif radios.get(id):
                    radio = radios[id]
                    if (current.get(radio) or {}).get(".type") != "wifi-device":
                        raise ValueError(f"no radio {radio}")
                    if await can_mesh(ubus) is False:
                        raise ValueError("its Wi-Fi software can't do mesh (install wpad-mesh-mbedtls)")
                    mesh_radios = {radio}
                    plan[id] = {"set": [], "add": radio}
                else:
                    outcome[id] = "left out (no mesh, no radio chosen)"
                    continue
                plan[id] |= {
                    "session": s,
                    "radios": {r: current.get(r) or {} for r in mesh_radios},
                }
            except Exception as e:
                outcome[id] = f"failed: {str(e)[:160] or type(e).__name__}"
        if any(o.startswith("failed") for o in outcome.values()):
            return {id: outcome.get(id, "not changed") for id in members}
        bands = {r.get("band") or r.get("hwmode") for p in plan.values() for r in p["radios"].values()}
        if len(bands) > 1:
            raise ValueError(
                f"the mesh radios are on different bands ({', '.join(map(str, bands))}); choose one"
            )
        if channel is None:
            channels = {str(r.get("channel") or "auto") for p in plan.values() for r in p["radios"].values()}
            if len(channels) != 1 or "auto" in channels:
                raise ValueError(
                    f"the mesh radios are on channels {', '.join(sorted(channels))}; choose one fixed channel"
                )
        # Staged on every member first, applied only when all are.
        staged: dict[str, set[str]] = {}
        before: dict[str, dict[str, bool]] = {}
        at = ""
        try:
            for id, p in plan.items():
                at, ubus, s = id, members[id], p["session"]
                sections = set(p["set"])
                for n in p["set"]:
                    # On again if it was switched off: a disabled section never gets an interface.
                    values = {"mesh_id": mesh_id, "key": key, "encryption": "sae", "disabled": "0"}
                    await ubus.call(
                        "uci", "set", {"config": "wireless", "section": n, "values": values}, session=s
                    )
                if p["add"]:
                    added = await ubus.call(
                        "uci",
                        "add",
                        {
                            "config": "wireless",
                            "type": "wifi-iface",
                            "values": {
                                "device": p["add"],
                                "mode": "mesh",
                                "mesh_id": mesh_id,
                                "encryption": "sae",
                                "key": key,
                                "network": "lan",
                                "mesh_fwding": "1",
                            },
                        },
                        session=s,
                    )
                    sections.add(str(added.get("section", "")))
                if channel is not None:
                    for r in p["radios"]:
                        await ubus.call(
                            "uci",
                            "set",
                            {"config": "wireless", "section": r, "values": {"channel": channel}},
                            session=s,
                        )
                before[id] = await ow._radios_up(ubus, s)
                staged[id] = sections
        except BaseException as e:
            for id in plan:
                with contextlib.suppress(Exception):
                    await members[id].call(
                        "uci", "revert", {"config": "wireless"}, session=plan[id]["session"]
                    )
            if not isinstance(e, Exception):
                raise
            return {
                id: f"failed: {str(e)[:160] or type(e).__name__}" if id == at else "not changed"
                for id in members
            }
        note(f"applying on {', '.join(sorted(staged))} at once")
        applied = await asyncio.gather(
            *(
                members[id].call(
                    "uci", "apply", {"rollback": True, "timeout": 120}, session=plan[id]["session"]
                )
                for id in staged
            ),
            return_exceptions=True,
        )
        refused = {id: e for id, e in zip(staged, applied, strict=True) if isinstance(e, BaseException)}
        good: set[str] = set()
        loop = asyncio.get_running_loop()
        until = loop.time() + deadline
        while not refused and loop.time() < until and good != set(staged):
            await asyncio.sleep(wait)
            for id in set(staged) - good:
                with contextlib.suppress(Exception):
                    if await _staged_ok(members[id], plan[id]["session"], before[id], staged[id]):
                        good.add(id)
                        note(f"{id}: back, mesh up")
        if good == set(staged):
            for id in staged:
                await members[id].call("uci", "confirm", {}, session=plan[id]["session"])
                outcome[id] = f"added on {plan[id]['add']}" if plan[id]["add"] else "set"
        else:
            # All or none. Those that applied are rolled back (else they would by themselves when
            # the rollback time is up); one that refused still has it staged: reverted.
            for id in staged:
                with contextlib.suppress(Exception):
                    if id in refused:
                        await members[id].call(
                            "uci", "revert", {"config": "wireless"}, session=plan[id]["session"]
                        )
                    else:
                        await members[id].call("uci", "rollback", {}, session=plan[id]["session"])
            missing = ", ".join(sorted(set(staged) - good))
            for id in staged:
                e = refused.get(id)
                outcome[id] = (
                    f"failed: {str(e)[:160] or type(e).__name__}"
                    if e
                    else f"failed: undone on all ({missing} didn't come back with the mesh up)"
                    if not refused
                    else "failed: undone on all (another member refused it)"
                )
    for id in members:
        outcome.setdefault(id, "not changed")
        note(f"{id}: {outcome[id]}")
    return outcome
