"""The Wi-Fi page over HTTP (wifi.py): networks by name across every access point, the radios,
scans and the channel plan. Seeing: admins. Changing and scanning: signed-in admins, as
streamed jobs (one access point after the other, each with its rollback), audited without
passwords."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import demo_more, roaming, setup_run, wifi
from .auth_api import client_address, require_admin, require_signin
from .fritzbox import client as fb
from .openwrt import client as ow
from .openwrt import sync
from .secrets import FINGERPRINT_SALT

if TYPE_CHECKING:
    from .hub import Hub


class NetworkChange(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)  # the network, by its name now
    new_ssid: str | None = Field(default=None, max_length=64)
    key: str | None = Field(default=None, max_length=64)
    encryption: str | None = Field(default=None, max_length=20)
    enabled: bool | None = None
    devices: list[str] | None = Field(default=None, max_length=64)  # only these (default: all)


class Target(BaseModel):
    device: str = Field(max_length=64)
    radio: str = Field(max_length=64)


class NetworkAdd(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    key: str = Field(default="", max_length=64)
    encryption: str = Field(default="sae-mixed", max_length=20)
    targets: list[Target] = Field(min_length=1, max_length=32)


class NetworkRemove(BaseModel):
    ssid: str = Field(min_length=1, max_length=64)
    targets: list[Target] = Field(min_length=1, max_length=32)  # radio: the network's section


class RadioChange(BaseModel):
    device: str = Field(max_length=64)
    radio: str = Field(max_length=64)
    channel: str | None = Field(default=None, max_length=8)  # "auto" or a number
    htmode: str | None = Field(default=None, max_length=10)
    txpower: int | None = Field(default=None, ge=0, le=36)
    country: str | None = Field(default=None, pattern=r"^([A-Z]{2}|00)$")


class PlanApply(BaseModel):
    radios: list[RadioChange] = Field(min_length=1, max_length=64)
    before: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    after: float | None = Field(default=None, ge=0, allow_inf_nan=False)


LIVE_WAIT = 10.0  # seconds one access point may take
LIVE_FRESH = 300.0  # seconds a reading is shown as it is (Kastellan's own changes end it sooner)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/wifi")
    scans = hub.wifi_scans

    def salt() -> bytes:
        return hub.own_secret(FINGERPRINT_SALT).encode()

    def openwrt_ids() -> list[str]:
        return [id for id, d in hub.config.devices.items() if d.openwrt]

    def fritz_ids() -> list[str]:
        return [id for id, d in hub.config.devices.items() if d.fritzbox]

    def members() -> dict[str, ow.Ubus | Exception]:
        out: dict[str, ow.Ubus | Exception] = {}
        for id in openwrt_ids():
            try:
                out[id] = hub.ubus(id)
            except Exception as e:  # its secret isn't there, or the key ring is locked
                out[id] = e
        return out

    def fritz_networks() -> dict[str, list[dict[str, Any]]]:
        out = {}
        for id in fritz_ids():
            st = hub.monitor.status.get(id)
            if st and st.fritz:
                out[id] = st.fritz.get("wifi") or []
        return out

    def router_settings() -> dict[str, dict[str, Any]]:
        """Each OpenWrt device's radios as its last report says (read ability only)."""
        out = {}
        for id in openwrt_ids():
            st = hub.monitor.status.get(id)
            radios = (st.router or {}).get("radios") if st else None
            if radios:
                out[id] = {
                    "radios": [
                        {
                            "section": r["name"],
                            "band": r.get("band"),
                            "channel": r.get("channel"),
                            "htmode": r.get("htmode"),
                            "clients": sum(len(i.get("clients") or []) for i in r.get("interfaces") or []),
                            "up": r.get("up"),
                            "disabled": r.get("disabled"),
                            "country": r.get("country"),
                        }
                        for r in radios
                    ]
                }
        return out

    def signed_in(request: Request):
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is changed")
        return w

    def job(action: str, request: Request, w, work, **logged: Any) -> StreamingResponse:
        audit = partial(hub.audit.record, f"wifi.{action}", w.user, client_address(request.scope), **logged)

        async def then_read_again(say):
            try:
                return await work(say)
            finally:
                invalidate()

        return setup_run.stream_work(then_read_again, audit)

    # What the access points say live (their Wi-Fi configuration, password fingerprints): asked
    # of all at once, each within LIVE_WAIT, and kept LIVE_FRESH seconds; older, it is shown
    # while a fresh reading is under way. A reading begun before `invalidate` still answers who
    # waits for it, but isn't kept (its generation is over).
    cache: dict[str, Any] = {"t": 0.0, "value": None, "task": None, "context": None, "generation": 0}

    def invalidate() -> None:
        cache["generation"] += 1
        cache.update(t=0.0, value=None, task=None)

    async def read_live() -> tuple[dict[str, Any], dict[tuple[str, int], str | None]]:
        key = salt()

        async def one(id: str, ubus: Any) -> dict[str, Any]:
            try:
                return await asyncio.wait_for(sync.survey({id: ubus}, key), LIVE_WAIT)
            except TimeoutError:
                return {"errors": {id: "didn't answer in time"}, "networks": []}

        parts = await asyncio.gather(*(one(i, u) for i, u in members().items()))

        async def fingerprint(id: str, index: int) -> tuple[tuple[str, int], str | None]:
            try:
                secret = await asyncio.wait_for(fb.wifi_key(hub.fritz(id), index), LIVE_WAIT)
                return (id, index), sync.fingerprint(secret, key)
            except Exception:
                return (id, index), None

        found = await asyncio.gather(
            *(fingerprint(id, n["index"]) for id, nets in fritz_networks().items() for n in nets)
        )
        return wifi.merge(parts), dict(found)

    async def live(auto: bool = False) -> tuple[dict[str, Any], dict[tuple[str, int], str | None]]:
        # A config reload or a locked ring must not leave old credentials' readings in use.
        try:
            context = (id(hub.config), salt())
        except Exception:
            invalidate()
            raise
        if context != cache["context"]:
            invalidate()
            cache["context"] = context
        age = time.monotonic() - cache["t"]
        # Fresh enough, or the page refreshing on its own with nobody using it (it reads the
        # access points anew when someone looks again): what is kept.
        if cache["value"] is not None and (age < LIVE_FRESH or auto):
            return cache["value"]
        if cache["task"] is None or cache["task"].done():

            async def refresh() -> tuple[dict[str, Any], dict[tuple[str, int], str | None]]:
                generation = cache["generation"]
                value = await read_live()
                if cache["generation"] == generation:
                    cache["t"], cache["value"] = time.monotonic(), value
                return value

            cache["task"] = hub.spawn(refresh(), name="Wi-Fi reading")
        if cache["value"] is not None:  # stale: shown now, the fresh one comes with the next look
            return cache["value"]
        return await asyncio.shield(cache["task"])

    @api.get("")
    async def overview(request: Request) -> dict[str, Any]:
        require_admin(request)
        fritz = fritz_networks()
        if hub.demo:
            survey, keys = demo_more.WIFI_SURVEY, {}
        else:
            survey, keys = await live(auto=request.headers.get("x-kastellan-auto") == "1")
        settings = router_settings()
        all_scans = scans.all(time.time() - 7 * 86400)
        radios = wifi.radios_from(settings, all_scans, fritz)
        newest: dict[str, dict[str, Any]] = {}
        for s in all_scans:
            newest[f"{s['device']}/{s['radio']}"] = s
        radio_rows = []
        for r in radios:
            st = next(
                (x for x in settings.get(r.device, {}).get("radios", []) if x["section"] == r.radio), {}
            )
            fr = next((n for n in fritz.get(r.device, []) if f"wlan{n['index']}" == r.radio), {})
            scan = newest.get(r.key) or {}
            busy = r.busy.get(r.channel or 0)
            if fr:
                up = fr.get("status") in (None, "Up")
                state = "off" if not fr.get("enabled") else "on" if up else "down"
            else:
                state = "off" if st.get("disabled") else "down" if st.get("up") is False else "on"
            mesh = next(
                (
                    m.get("mesh_id") or ""
                    for m in survey.get("mesh") or []
                    for e in m.get("on") or []
                    if e["device"] == r.device and e.get("radio") == r.radio
                ),
                None,
            )
            radio_rows.append(
                {
                    "device": r.device,
                    "radio": r.radio,
                    "kind": "fritzbox" if fr else "openwrt",
                    "band": r.band,
                    "channel": r.channel,
                    "width": r.width,
                    "htmode": st.get("htmode"),
                    "clients": st.get("clients", fr.get("clients")),
                    "neighbours": len(r.neighbours),
                    "hears": r.hears,
                    "busy": busy,
                    "scanned": scan.get("t"),
                    "allowed": r.allowed,
                    "country": st.get("country"),
                    "state": state,
                    "off": state == "off",
                    "mesh": mesh,
                }
            )
        countries = sorted({r["country"] for r in radio_rows if r.get("country")})
        networks = wifi.lineup(survey, fritz, keys)
        clients = clients_view()
        country_note = wifi.country_note(countries, [r for r in radio_rows if r["kind"] == "openwrt"])
        errors = survey.get("errors") or {}
        found = wifi.findings(
            networks=networks,
            radios=radio_rows,
            clients=clients,
            errors=errors,
            country=country_note,
            meshes=survey.get("mesh") or [],
            fritz_mesh=fritz_meshes(),
            roaming=roaming_checks(survey),
            scanned=bool(all_scans),
            names={id: hub.config.label(id) for id in hub.config.devices},
        )
        return {
            "networks": networks,
            "clients": clients,
            "countries": countries,
            "country_note": country_note,
            "radios": radio_rows,
            "findings": found,
            "errors": errors,
            "scans": {k: _scan_view(v) for k, v in newest.items()},
            "plans": scans.plans()[-5:],
            "scanning": sorted(hub.wifi_scanning),
            "scan_at": hub.config.wifi.scan_at,
        }

    @api.post("/networks/change")
    async def change(body: NetworkChange, request: Request) -> StreamingResponse:
        """Name, password, encryption, on/off of a network everywhere it is (or on `devices`)."""
        w = signed_in(request)
        for k, v in (("ssid", body.new_ssid), ("key", body.key), ("encryption", body.encryption)):
            if v:
                try:
                    ow.check_value(k, v)
                except ValueError as e:
                    raise HTTPException(400, str(e)) from e
        wanted = set(body.devices or [])
        for id, nets in fritz_networks().items():
            if body.devices is not None and id not in wanted:
                continue
            matching = [n for n in nets if n.get("ssid") == body.ssid]
            if not matching:
                continue
            try:
                if body.new_ssid is not None:
                    fb.check_ssid(body.new_ssid)
                if body.key is not None:
                    fb.check_key(body.key)
                if body.encryption and body.encryption not in fb.BEACON:
                    raise ValueError(f"{id}: a FRITZ!Box takes WPA2, WPA3, WPA2/WPA3 or WPA/WPA2")
                if body.encryption and any(
                    wifi.encryption_word(n.get("encryption")) != wifi.encryption_word(body.encryption)
                    for n in matching
                ):
                    raise ValueError(f"{id}: change encryption in FRITZ!OS first")
            except ValueError as e:
                raise HTTPException(400, str(e)) from e

        async def work(say):
            outcome: dict[str, str] = {}
            values = {"ssid": body.new_ssid, "key": body.key, "encryption": body.encryption}
            values = {k: v for k, v in values.items() if v}
            if body.enabled is not None:
                values["disabled"] = not body.enabled
            ow_members = {i: u for i, u in members().items() if body.devices is None or i in wanted}
            if values:
                outcome |= await sync.change_network(ow_members, body.ssid, values, say)
            for id, nets in fritz_networks().items():
                if body.devices is not None and id not in wanted:
                    continue
                for n in nets:
                    if n.get("ssid") != body.ssid:
                        continue
                    try:
                        await fb.change_wifi(
                            hub.fritz(id),
                            n["index"],
                            {
                                "ssid": body.new_ssid,
                                "key": body.key,
                                "enabled": body.enabled,
                                "encryption": body.encryption,
                            },
                        )
                        outcome[f"{id}/wlan{n['index']}"] = "done"
                    except Exception as e:
                        outcome[f"{id}/wlan{n['index']}"] = f"failed: {str(e)[:160]}"
                    say(f"{id} wlan{n['index']}: {outcome[f'{id}/wlan{n["index"]}']}")
            return {"outcome": outcome}

        logged = {
            "ssid": body.ssid,
            "new_ssid": body.new_ssid,
            "key": "***" if body.key else None,
            "encryption": body.encryption,
            "enabled": body.enabled,
            "devices": body.devices,
        }
        return job("network", request, w, work, **logged)

    @api.post("/networks/add")
    async def add(body: NetworkAdd, request: Request) -> StreamingResponse:
        """The network on more access points (OpenWrt radios)."""
        w = signed_in(request)
        for t in body.targets:
            d = hub.config.devices.get(t.device)
            if d is None or d.openwrt is None:
                raise HTTPException(400, f"{t.device}: only OpenWrt access points get new networks from here")

        async def work(say):
            outcome = {}
            for t in body.targets:
                try:
                    section = await ow.add_network(
                        hub.ubus(t.device), t.radio, body.ssid, body.key, body.encryption
                    )
                    outcome[f"{t.device}/{t.radio}"] = f"added ({section})"
                except Exception as e:
                    outcome[f"{t.device}/{t.radio}"] = f"failed: {str(e)[:160]}"
                say(f"{t.device} {t.radio}: {outcome[f'{t.device}/{t.radio}']}")
            return {"outcome": outcome}

        return job("add", request, w, work, ssid=body.ssid, targets=[t.model_dump() for t in body.targets])

    @api.post("/networks/remove")
    async def remove(body: NetworkRemove, request: Request) -> StreamingResponse:
        w = signed_in(request)

        async def work(say):
            outcome = {}
            for t in body.targets:
                d = hub.config.devices.get(t.device)
                try:
                    if d is None or d.openwrt is None:
                        raise ValueError("a FRITZ!Box's networks are switched off, not removed")
                    await ow.remove_network(hub.ubus(t.device), t.radio, expected_ssid=body.ssid)
                    outcome[f"{t.device}/{t.radio}"] = "removed"
                except Exception as e:
                    outcome[f"{t.device}/{t.radio}"] = f"failed: {str(e)[:160]}"
                say(f"{t.device} {t.radio}: {outcome[f'{t.device}/{t.radio}']}")
            return {"outcome": outcome}

        return job("remove", request, w, work, ssid=body.ssid, targets=[t.model_dump() for t in body.targets])

    async def set_radio(r: RadioChange, say=None) -> str:
        d = hub.config.devices.get(r.device)
        if d is None:
            raise ValueError("no such device")
        if d.fritzbox is not None and r.radio.startswith("wlan"):
            if r.channel is None or r.htmode is not None or r.txpower is not None or r.country:
                raise ValueError("FRITZ!Box: only its channel can be changed here")
            channel = 0 if r.channel == "auto" else int(r.channel)
            await fb.change_wifi(hub.fritz(r.device), int(r.radio[4:]), {"channel": channel})
            return "done"
        if d.openwrt is None:
            raise ValueError("no OpenWrt or FRITZ!Box login")
        values = {
            k: v
            for k, v in (
                ("channel", r.channel),
                ("htmode", r.htmode),
                ("txpower", r.txpower),
                ("country", r.country),
            )
            if v is not None
        }
        await ow.change_wifi(hub.ubus(r.device), {r.radio: values})
        return "done"

    @api.put("/radios")
    async def radio(body: RadioChange, request: Request) -> dict[str, Any]:
        w = signed_in(request)
        audit = partial(
            hub.audit.record, "wifi.radio", w.user, client_address(request.scope), **body.model_dump()
        )
        try:
            await set_radio(body)
        except Exception as e:
            audit(outcome=f"failed: {str(e)[:120]}")
            raise HTTPException(400 if isinstance(e, ValueError) else 502, str(e)[:300]) from e
        invalidate()
        audit(outcome="done")
        return {"done": True}

    @api.post("/scan")
    async def scan(request: Request) -> StreamingResponse:
        """Scan with every OpenWrt radio whose login may (clients may pause a moment)."""
        w = signed_in(request)

        async def work(say):
            return {"scanned": await scan_all(hub, say)}

        return job("scan", request, w, work)

    @api.get("/plan")
    async def suggest(request: Request, dfs: bool = Query(default=False)) -> dict[str, Any]:
        """A channel plan from the newest scans (a suggestion: nothing changes)."""
        require_admin(request)
        radios = wifi.radios_from(router_settings(), scans.all(time.time() - 7 * 86400), fritz_networks())
        result = await asyncio.to_thread(
            wifi.plan, radios, dfs, {id: d.name for id, d in hub.config.devices.items()}
        )
        result["scanned"] = bool(scans.all(time.time() - 7 * 86400))
        return result

    def fritz_meshes() -> dict[str, dict[str, Any]]:
        out = {}
        for id in fritz_ids():
            st = hub.monitor.status.get(id)
            mesh = (st.fritz or {}).get("mesh") if st else None
            if mesh and mesh.get("members"):
                out[id] = mesh
        return out

    def roaming_checks(survey: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """roaming.check for each group of two or more OpenWrt access points (what the Groups
        page says), from the survey at hand: nothing is asked of the devices again."""
        out = {}
        for gid, g in hub.config.groups.items():
            ids = [id for id in g.devices if (d := hub.config.devices.get(id)) and d.openwrt]
            if len(ids) >= 2:
                out[gid] = roaming.check(hub, ids, wifi.of_members(survey, ids))
        return out

    def clients_view() -> list[dict[str, Any]]:
        """Every Wi-Fi client with how its radio link is (wifi.diagnose): weak, slow, on 2.4 GHz
        where 5 GHz is near, ..."""
        out = []
        for id in openwrt_ids():
            st = hub.monitor.status.get(id)
            for r in ((st.router or {}).get("radios") or []) if st else []:
                for i in r.get("interfaces") or []:
                    for c in i.get("clients") or []:
                        out.append(
                            {
                                **c,
                                "device": id,
                                "radio": r.get("name"),
                                "band": r.get("band"),
                                "ssid": i.get("ssid"),
                                "kind": "openwrt",
                            }
                        )
        for id, nets in fritz_networks().items():
            # The Wi-Fi list has no names; the box's host list has them, by MAC address.
            st = hub.monitor.status.get(id)
            hosts = (st.fritz or {}).get("clients") or [] if st else []
            names = {str(h.get("mac") or "").lower(): h.get("name") for h in hosts if h.get("mac")}
            for n in nets:
                for c in n.get("associated") or []:
                    out.append(
                        {
                            "name": names.get(str(c.get("mac") or "").lower()) or None,
                            **c,
                            "device": id,
                            "radio": f"wlan{n.get('index')}",
                            "band": n.get("band"),
                            "ssid": n.get("ssid"),
                            "kind": "fritzbox",
                        }
                    )
        for c in out:
            c["issues"] = wifi.diagnose(c)
        out.sort(key=lambda c: (-len(c["issues"]), c.get("signal") or 0))
        return out[:300]

    @api.post("/plan/apply")
    async def apply(body: PlanApply, request: Request) -> StreamingResponse:
        """The chosen radios to their suggested channels, one access point after the other."""
        w = signed_in(request)

        async def work(say):
            outcome = {}
            for r in body.radios:
                key = f"{r.device}/{r.radio}"
                try:
                    outcome[key] = await set_radio(r)
                except Exception as e:
                    outcome[key] = f"failed: {str(e)[:160]}"
                say(f"{key} → {r.channel}{' ' + r.htmode if r.htmode else ''}: {outcome[key]}")
            scans.note_plan(
                {
                    "t": time.time(),
                    "by": w.user,
                    "radios": [r.model_dump() for r in body.radios],
                    "before": body.before,
                    "after": body.after,
                    "outcome": outcome,
                }
            )
            if any(v == "done" for v in outcome.values()):
                say("A scan follows in two minutes, to see how the air turned out.")

                async def scan_later() -> None:
                    await asyncio.sleep(120)
                    with contextlib.suppress(Exception):
                        await scan_all(hub)

                hub.spawn(scan_later(), name="Wi-Fi scan after a plan")
            return {"outcome": outcome}

        return job("plan", request, w, work, radios=[r.model_dump() for r in body.radios])

    return api


async def scan_all(hub: Hub, say=None) -> int:
    """Scan with every OpenWrt radio whose login may; the scans are kept. How many radios."""
    say = say or (lambda line: None)
    found = []
    for id, d in list(hub.config.devices.items()):
        if d.openwrt is None or id in hub.wifi_scanning:
            continue
        st = hub.monitor.status.get(id)
        radios = (st.router or {}).get("radios") if st else None
        if not radios:
            say(f"{id}: its radios aren't known yet")
            continue
        hub.wifi_scanning.add(id)
        try:
            ubus = hub.ubus(id)
            for r in radios:
                ifaces = [i for i in r.get("interfaces") or [] if i.get("ifname")]
                ap = next((i for i in ifaces if i.get("mode") == "ap"), ifaces[0] if ifaces else None)
                if r.get("disabled") or ap is None:
                    continue
                entry: dict[str, Any] = {
                    "t": time.time(),
                    "device": id,
                    "radio": r["name"],
                    "band": r.get("band"),
                    "channel": r.get("channel"),
                }
                try:
                    entry["neighbours"] = await ow.scan(ubus, ap["ifname"])
                except ow.UbusError as e:
                    if e.code in (6, ow.DENIED):
                        say(f"{id}: not allowed (tick “Scan for neighbouring Wi-Fi networks” in its setup)")
                        break
                    raise
                for key, call in (("survey", ow.survey), ("freqs", ow.freqlist)):
                    try:
                        entry[key] = await call(ubus, ap["ifname"])
                    except Exception:
                        entry[key] = []
                bssids = []
                for i in ifaces:
                    try:
                        info = await ow.iface_info(ubus, i["ifname"])
                        if info["bssid"]:
                            bssids.append(info["bssid"])
                    except Exception:
                        pass
                entry["bssids"] = bssids
                found.append(entry)
                say(f"{id} {r['name']} ({r.get('band')}): {len(entry['neighbours'])} networks heard")
        except Exception as e:
            say(f"{id}: {str(e)[:160] or type(e).__name__}")
        finally:
            hub.wifi_scanning.discard(id)
    for id, d in list(hub.config.devices.items()):
        if d.fritzbox is None or id in hub.wifi_scanning:
            continue
        hub.wifi_scanning.add(id)
        try:
            found += await scan_fritz(hub, id)
            say(f"{id}: its Wi-Fi environment read")
        except Exception as e:
            say(f"{id}: {str(e)[:160] or type(e).__name__}")
        finally:
            hub.wifi_scanning.discard(id)
    if found:
        await asyncio.to_thread(hub.wifi_scans.add, found)
    return len(found)


def _mhz(channel: int, band: str) -> int:
    return 2407 + 5 * channel if band == "2g" else 5000 + 5 * channel


async def scan_fritz(hub: Hub, id: str) -> list[dict[str, Any]]:
    """A FRITZ!Box's Wi-Fi environment (it scans by itself; the web interface's `chan` page) as
    scans like an OpenWrt radio's, one per band, under its network of that band (wlanN)."""
    from .fritzbox import web

    st = hub.monitor.status.get(id)
    nets = [n for n in ((st.fritz or {}).get("wifi") or []) if not n.get("guest")] if st else []
    env = web.environment(await hub.fritz_web(id).data("chan", "environment"))
    out = []
    for band, b in env.items():
        net = next((n for n in nets if n.get("band") == band), None)
        if net is None:
            continue
        out.append(
            {
                "t": time.time(),
                "device": id,
                "radio": f"wlan{net['index']}",
                "band": band,
                "channel": b["channel"] or net.get("channel"),
                "neighbours": b["neighbours"],
                "survey": [
                    {"mhz": _mhz(b["channel"], band), "busy": b["busy"], "noise": None, "in_use": True}
                ]
                if b["busy"] is not None and b["channel"]
                else [],
                "freqs": [
                    {
                        "channel": c["channel"],
                        "mhz": _mhz(c["channel"], band),
                        "restricted": c["dfs"],
                        "active": False,
                    }
                    for c in b["channels"]
                ],
                "bssids": [n["bssid"] for n in nets if n.get("bssid")],
                "auto": b["auto"],
            }
        )
    return out


def due(scan_at: str | None, last: str | None, now: datetime) -> bool:
    """Whether the daily scan is due: past `scan_at` (local "HH:MM") today and not yet run today."""
    if not scan_at:
        return False
    h, m = (int(x) for x in scan_at.split(":"))
    today = now.date().isoformat()
    return (
        (now.hour, now.minute) >= (h, m)
        and last != today
        and (now.hour * 60 + now.minute) - (h * 60 + m) < 120
    )


async def run(hub: Hub) -> None:
    """The daily scan at `wifi.scan_at` (a quiet hour), if any radio may scan."""
    log = logging.getLogger(__name__)
    marker = hub.wifi_scans.folder / "last-scheduled"
    while True:
        await asyncio.sleep(60)
        if hub.demo:
            continue
        try:
            last = marker.read_text().strip() if marker.exists() else None
            now = datetime.now().astimezone()
            if due(hub.config.wifi.scan_at, last, now):
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(now.date().isoformat())
                n = await scan_all(hub)
                log.info("Wi-Fi: scheduled scan of %d radios", n)
        except Exception:
            log.exception("the scheduled Wi-Fi scan failed")


def _scan_view(s: dict[str, Any]) -> dict[str, Any]:
    """A scan as the page shows it: neighbours by channel."""
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for n in s.get("neighbours") or []:
        by_channel.setdefault(str(n.get("channel")), []).append(
            {"ssid": n.get("ssid") or "(hidden)", "signal": n.get("signal"), "width": n.get("width")}
        )
    return {
        "t": s.get("t"),
        "band": s.get("band"),
        "channel": s.get("channel"),
        "neighbours": by_channel,
        "survey": [x for x in s.get("survey") or [] if x.get("busy") is not None],
    }
