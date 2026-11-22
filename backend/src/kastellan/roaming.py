"""Wi-Fi roaming in a group of access points. The access points decide (usteer, 802.11r/k/v):
Kastellan only looks whether they are set up to work together, shows how clients roam, raises
alarms when that breaks, and can line the settings up. Wi-Fi never depends on the hub.

What a group needs to roam well:
- per network on several members: 802.11r (fast transition) on, one mobility domain for all,
  802.11k and v (neighbour reports, steering) where the Wi-Fi software can;
- channels that don't overlap between neighbours (2.4 GHz: 1, 6, 11; 5 GHz: apart), unless a
  mesh links them (a mesh needs one channel);
- usteer running on every member, and each seeing the others;
- enough memory on small devices (32 MB ones struggle with usteer and the rest).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .alarms import Problem, Severity
from .openwrt.client import SMALL_MEMORY

if TYPE_CHECKING:
    from .hub import Hub

LOW_FREE = 8 * 2**20

# What roaming assistance sets on each member's network (with its mobility domain).
FT_OPTIONS = {
    "ieee80211r": "1",
    "ft_over_ds": "0",
    "ft_psk_generate_local": "1",
    "ieee80211k": "1",
    "bss_transition": "1",
}


def mobility_domain(ssid: str) -> str:
    """The same 4 hex digits for a network everywhere (from its name), as hostapd wants it."""
    return hashlib.sha256(ssid.encode()).hexdigest()[:4]


def _usteer(hub: Hub, id: str) -> str:
    """running, stopped, missing, unknown (the router's service list, or usteer's own answer)."""
    status = hub.monitor.status.get(id)
    services = ((status.router or {}).get("services") if status else None) or []
    for s in services:
        if s.get("name") == "usteer":
            return "running" if s.get("running") else "stopped"
    if (hub.roaming.get(id) or {}).get("nodes"):
        return "running"
    return "missing" if services else "unknown"


def _address(hub: Hub, id: str) -> str | None:
    d = hub.config.devices.get(id)
    if d is None or not d.host:
        return None
    return hub.resolver.address(d.host) or d.host


def check(hub: Hub, members: list[str], survey: dict[str, Any] | None) -> dict[str, Any]:
    """Findings for a group (each: level ok/info/warning, text, what to change, devices), and
    per member: usteer's state, who it sees, its memory."""
    findings: list[dict[str, Any]] = []

    def say(
        kind: str,
        level: str,
        text: str,
        fix: str = "",
        devices: list[str] | None = None,
        ssid: str | None = None,
    ) -> None:
        findings.append(
            {"kind": kind, "level": level, "text": text, "fix": fix, "devices": devices or [], "ssid": ssid}
        )

    def names(ids: Any) -> str:
        return ", ".join(hub.config.label(i) if i in hub.config.devices else i for i in ids)

    # Networks across members: 802.11r/k/v and one mobility domain.
    shared = [n for n in (survey or {}).get("networks") or [] if len(n.get("devices") or []) >= 2]
    for n in shared:
        on = n.get("on") or []
        no_ft = sorted({e["device"] for e in on if not e.get("ft")})
        domains = {e.get("mobility_domain") for e in on if e.get("ft")}
        if no_ft:
            say(
                "ft",
                "warning",
                f"{n['ssid']}: fast roaming (802.11r) is off on {names(no_ft)}",
                "Roaming assistance turns it on with one mobility domain for all",
                no_ft,
                n["ssid"],
            )
        elif len(domains) > 1:
            say(
                "domain",
                "warning",
                f"{n['ssid']}: different mobility domains ({', '.join(sorted(str(d) for d in domains))})",
                "One domain for the whole network: clients only fast-roam within one",
                sorted({e["device"] for e in on}),
                n["ssid"],
            )
        no_kv = sorted({e["device"] for e in on if not (e.get("k") and e.get("v"))})
        if no_kv and not no_ft:
            say(
                "kv",
                "info",
                f"{n['ssid']}: neighbour reports and steering (802.11k/v) are off on {names(no_kv)}",
                "They help clients pick the better access point (needs wpad, not wpad-basic)",
                no_kv,
                n["ssid"],
            )
    # Channels: neighbours on the same (or, at 2.4 GHz, overlapping) channel.
    meshed = {
        (e["device"], e["radio"])
        for m in (survey or {}).get("mesh") or []
        for e in m.get("on") or []
        if e.get("radio")
    }
    by_band: dict[str, list[tuple[str, str, Any]]] = {}
    for id, radios in ((survey or {}).get("radios") or {}).items():
        for r in radios:
            by_band.setdefault(str(r.get("band")), []).append((id, r.get("section"), r.get("channel")))
    for band, radios in by_band.items():
        auto = sorted({id for id, _, ch in radios if ch in (None, "auto", "0")})
        if auto and len(radios) > 1:
            say(
                "auto",
                "info",
                f"{band}: channel chosen automatically on {names(auto)}",
                "Fixed channels keep neighbours apart (Wi-Fi page: channel plan)",
                auto,
            )
        fixed = [(id, sec, str(ch)) for id, sec, ch in radios if ch not in (None, "auto", "0")]
        seen: dict[str, list[str]] = {}
        for id, sec, ch in fixed:
            if (id, sec) in meshed:
                continue
            seen.setdefault(ch, []).append(id)
        for ch, ids in seen.items():
            if len(set(ids)) > 1:
                say(
                    "channel",
                    "warning",
                    f"{band}: {names(sorted(set(ids)))} share channel {ch}",
                    "Neighbours on one channel take turns; the channel plan spreads them",
                    sorted(set(ids)),
                )
        if band == "2g":
            odd = sorted({id for id, _, ch in fixed if ch not in ("1", "6", "11")})
            if odd:
                say(
                    "odd",
                    "info",
                    f"2.4 GHz: {names(odd)} on a channel other than 1, 6 or 11",
                    "Others overlap two of those three",
                    odd,
                )
    # usteer on every member, each seeing the others; memory.
    state: dict[str, dict[str, Any]] = {}
    addresses = {id: _address(hub, id) for id in members}
    for id in members:
        r = hub.roaming.get(id) or {}
        hosts = {n.get("host") for n in r.get("nodes") or [] if not n.get("own")}
        sees = sorted(o for o in members if o != id and addresses.get(o) in hosts)
        status = hub.monitor.status.get(id)
        memory = (((status.router or {}).get("system") or {}).get("memory") or {}) if status else {}
        state[id] = {
            "usteer": _usteer(hub, id),
            "sees": sees,
            "misses": sorted(o for o in members if o != id and o not in sees)
            if r.get("nodes") and not r.get("error")
            else [],
            "clients": len(r.get("clients") or []),
            "error": r.get("error"),
            "memory": memory.get("total"),
            "free": memory.get("available", memory.get("free")),
            "config": r.get("config") or {},
        }
    without = sorted(id for id, s in state.items() if s["usteer"] in ("missing", "stopped"))
    if without and len(members) > 1:
        say(
            "usteer",
            "warning" if any(s["usteer"] == "running" for s in state.values()) else "info",
            f"usteer isn't running on {names(without)}",
            "usteer lets the access points steer clients together (opkg/apk: usteer, luci-app-usteer)",
            without,
        )
    for id, s in state.items():
        if s["misses"] and s["usteer"] == "running":
            say(
                "view",
                "warning",
                f"{names([id])} doesn't see {names(s['misses'])} (usteer)",
                "They must be in one network segment with usteer running (UDP 16720)",
                [id],
            )
        if isinstance(s["memory"], int) and s["memory"] < SMALL_MEMORY:
            say(
                "memory",
                "info",
                f"{names([id])} has {s['memory'] // 2**20} MB memory",
                "Small devices can run out with usteer: install from the package file, lower its"
                " OOM priority",
                [id],
            )
    # Thresholds that differ between members steer clients back and forth.
    triggers = {s["config"].get("roam_trigger_snr") for s in state.values() if s["config"]}
    if len(triggers) > 1:
        say(
            "trigger",
            "info",
            "usteer's roam trigger differs between members",
            "The same thresholds everywhere keep clients from bouncing",
            sorted(state),
        )
    return {"findings": findings, "members": state}


def assistance(survey: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    """What roaming assistance changes, per member: for each network on several members, its
    sections get 802.11r/k/v with the network's mobility domain (an existing common one stays)."""
    out: dict[str, dict[str, dict[str, str]]] = {}
    for n in survey.get("networks") or []:
        on = n.get("on") or []
        if len({e["device"] for e in on}) < 2:
            continue
        # Local FT key generation needs a shared personal-network password. Enterprise,
        # open networks and inconsistent credentials require their own configuration.
        if (
            not n.get("same_key")
            or not n.get("same_encryption")
            or not all(
                str(e.get("encryption") or "").split("+")[0] in {"psk2", "psk-mixed", "sae", "sae-mixed"}
                for e in on
            )
        ):
            continue
        domains = {e.get("mobility_domain") for e in on if e.get("mobility_domain")}
        md = next(iter(domains)) if len(domains) == 1 else mobility_domain(n["ssid"])
        for e in on:
            if not (e.get("ft") and e.get("mobility_domain") == md and e.get("k") and e.get("v")):
                out.setdefault(e["device"], {})[e["section"]] = {**FT_OPTIONS, "mobility_domain": md}
    return out


def problems(hub: Hub) -> list[Problem]:
    """Alarms for groups whose members roam together: usteer stopped where it ran, a member out
    of the others' view, a small device running out of memory."""
    out: list[Problem] = []
    for gid, g in hub.config.groups.items():
        members = [id for id in g.devices if (d := hub.config.devices.get(id)) and d.openwrt]
        if len(members) < 2:
            continue
        ran = [id for id in members if (hub.roaming.get(id) or {}).get("nodes")]
        if not ran:
            continue  # nobody here roams with usteer: nothing to watch
        state = check(hub, members, None)["members"]
        for id, s in state.items():
            status = hub.monitor.status.get(id)
            checked = status.router_checked if status else None
            if not checked or (datetime.now(UTC) - checked).total_seconds() > 300 or status.router_error:
                continue
            label = hub.config.label(id)
            gone = s["usteer"] == "stopped" or "not found" in str(s["error"] or "").lower()
            if id in ran and gone:
                out.append(
                    Problem(
                        f"roaming:{gid}:{id}:usteer",
                        Severity.WARNING,
                        id,
                        "usteer",
                        f"{label}: usteer stopped",
                        f"roaming in {g.name or gid} works without it, but clients aren't steered",
                    )
                )
            if s["misses"] and s["usteer"] == "running":
                out.append(
                    Problem(
                        f"roaming:{gid}:{id}:view",
                        Severity.WARNING,
                        id,
                        "usteer",
                        f"{label} doesn't see {', '.join(hub.config.label(m) for m in s['misses'])}",
                        "usteer: they don't exchange what they know (UDP 16720)",
                    )
                )
            free, total = s["free"], s["memory"]
            if isinstance(free, int) and isinstance(total, int) and total < SMALL_MEMORY and free < LOW_FREE:
                out.append(
                    Problem(
                        f"roaming:{gid}:{id}:memory",
                        Severity.WARNING,
                        id,
                        "its memory",
                        f"{label}: {free // 2**20} MB memory left",
                        f"of {total // 2**20} MB; services may be stopped when it runs out",
                    )
                )
    return out
