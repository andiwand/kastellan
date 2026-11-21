"""Wi-Fi across every access point Kastellan has a login on (OpenWrt, FRITZ!Box): the networks
by name, the radios, what the radios hear, and a channel plan from that.

Networks: the same name (SSID) on several access points is one network, wherever it runs;
passwords are compared by fingerprint (an HMAC with a key of Kastellan's), never shown.

Diagnostics come from scans (OpenWrt's `scan` ability: iwinfo scan and survey; a scan can make
clients pause a moment, so it runs on request): per radio the neighbouring networks (name,
channel, width, signal), how busy each channel was, and which of our own access points it hears.
Scans are kept (`<data>/wifi/scans.jsonl`, 30 days), since one scan is a moment.

The channel plan is a suggestion (`plan`): each band on its own, our radios apart from each
other first (they hear each other best), then away from loud neighbours and busy channels;
2.4 GHz only on 1, 6 or 11 at 20 MHz; 5 GHz in whole blocks of the radio's width; DFS channels
(radar: the radio must listen first and may have to move) only when asked. Every radio gets its
reason. Nothing is changed unless an admin applies it, one access point at a time with its
rollback.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KEEP_SCANS = 30 * 86400
CH24 = (1, 6, 11)
# 5 GHz blocks by width: the channels each block covers.
BLOCKS = {
    20: [
        [c]
        for c in (
            36,
            40,
            44,
            48,
            52,
            56,
            60,
            64,
            100,
            104,
            108,
            112,
            116,
            120,
            124,
            128,
            132,
            136,
            140,
            144,
            149,
            153,
            157,
            161,
            165,
        )
    ],
    40: [[c, c + 4] for c in (36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157)],
    80: [[c, c + 4, c + 8, c + 12] for c in (36, 52, 100, 116, 132, 149)],
    160: [[c + 4 * i for i in range(8)] for c in (36, 100)],
}
DFS = set(range(52, 145))
OWN_ASSUMED = -55  # dBm: two of our access points that don't report hearing each other


def width_of(htmode: str | None, band: str | None) -> int:
    m = re.search(r"(\d+)", htmode or "")
    if m:
        return int(m.group(1))
    return 20 if band == "2g" else 80


def span(channel: int, width: int, band: str) -> tuple[float, float]:
    """The frequency range (MHz) a network on `channel` with `width` occupies."""
    if band == "2g":
        centre = 2407 + 5 * channel if channel < 14 else 2484
        return centre - max(width, 20) / 2 - 1, centre + max(width, 20) / 2 + 1
    block = next((b for b in BLOCKS.get(width, BLOCKS[20]) if channel in b), [channel])
    low, high = 5000 + 5 * block[0] - 10, 5000 + 5 * block[-1] + 10
    return low, high


def overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def loudness(signal: float | None) -> float:
    """How much a network at `signal` dBm disturbs (dB above the noise, roughly)."""
    if signal is None:
        return 0.0
    return max(0.0, float(signal) + 92)


@dataclass
class Radio:
    device: str
    radio: str  # OpenWrt's radio section, or a FRITZ!Box's "wlanN"
    band: str  # 2g, 5g
    channel: int | None  # None: automatic
    width: int
    neighbours: list[dict[str, Any]] = field(default_factory=list)  # from its scan
    busy: dict[int, int] = field(default_factory=dict)  # channel -> percent (survey)
    allowed: list[int] | None = None  # channels it may use (freqlist); None: unknown
    hears: dict[str, float] = field(default_factory=dict)  # our other radios (device/radio) -> dBm
    htmode: str | None = None  # OpenWrt's (HE80, VHT40, ...); None for a FRITZ!Box

    @property
    def key(self) -> str:
        return f"{self.device}/{self.radio}"


def candidates(r: Radio, dfs: bool) -> list[int]:
    """Primary channels the radio could move to (whole blocks within what it may use)."""
    if r.allowed is None:
        return [r.channel] if r.channel else []  # never guess the radio's regulatory limits
    if r.band == "2g":
        return [c for c in CH24 if r.allowed is None or c in r.allowed]
    out = []
    for block in BLOCKS.get(r.width, BLOCKS[20]):
        if not dfs and any(c in DFS for c in block):
            continue
        if r.allowed is not None and any(c not in r.allowed for c in block):
            continue
        # A channel within the block it is on now stays (36 and 44 are the same 80 MHz).
        out.append(r.channel if r.channel in block else block[0])
    return out


def cost(
    r: Radio,
    channel: int,
    others: dict[str, int],
    radios: dict[str, Radio],
    names: dict[str, str] | None = None,
) -> tuple[float, list[str]]:
    """How much `r` would suffer on `channel` (with our other radios on `others`), and why."""
    width = 20 if r.band == "2g" else r.width
    mine = span(channel, width, r.band)
    total, why = 0.0, []
    near = [
        n
        for n in r.neighbours
        if n.get("channel") and overlap(mine, span(int(n["channel"]), int(n.get("width") or 20), r.band))
    ]
    if near:
        louder = sum(loudness(n.get("signal")) for n in near)
        total += louder
        strongest = max((n.get("signal") or -100) for n in near)
        why.append(f"{len(near)} network{'s' if len(near) != 1 else ''} next door (loudest {strongest} dBm)")
    busy = [r.busy[c] for c in r.busy if overlap(mine, span(c, 20, r.band))]
    if busy:
        total += max(busy) * 0.5
        if max(busy) >= 30:
            why.append(f"busy {max(busy)}% of the time")
    for key, ch in others.items():
        o = radios[key]
        if o.band != r.band or key == r.key:
            continue
        if overlap(mine, span(ch, 20 if o.band == "2g" else o.width, o.band)):
            heard = r.hears.get(key, o.hears.get(r.key, OWN_ASSUMED))
            total += 2 * loudness(heard)
            who = (names or {}).get(o.device, o.device)
            how = f"{heard:.0f} dBm" if key in r.hears or r.key in o.hears else "not scanned: assumed near"
            why.append(f"shares it with {who} ({how})")
    return total, why


@dataclass
class Suggestion:
    device: str
    radio: str
    band: str
    current: int | None
    channel: int
    width: int
    htmode: str | None
    reason: str
    before: float
    after: float

    def view(self) -> dict[str, Any]:
        return self.__dict__ | {"change": self.current != self.channel or self.htmode is not None}


def _assign(mine: list[Radio], by_key: dict[str, Radio], dfs: bool) -> tuple[dict[str, int], float] | None:
    """The best channel for each of `mine` (one band) and its total disturbance."""
    options = [candidates(r, dfs) or ([r.channel] if r.channel else []) for r in mine]
    if any(not o for o in options):
        return None

    def score(choice: dict[str, int]) -> float:
        return sum(cost(by_key[k], ch, choice, by_key)[0] for k, ch in choice.items())

    combos = 1
    for o in options:
        combos *= len(o)
    best: dict[str, int] = {}
    best_score: float | None = None
    if combos <= 20000:
        for combo in itertools.product(*options):
            choice = {r.key: c for r, c in zip(mine, combo, strict=True)}
            sc = score(choice)
            # Ties: keep what is set now (no change for nothing).
            sc += 0.01 * sum(1 for r, c in zip(mine, combo, strict=True) if r.channel != c)
            if best_score is None or sc < best_score:
                best, best_score = choice, sc
    else:  # many radios: one after the other, three rounds
        best = {r.key: (r.channel if r.channel in o else o[0]) for r, o in zip(mine, options, strict=True)}
        for _ in range(3):
            for r, o in zip(mine, options, strict=True):
                best[r.key] = min(o, key=lambda c, r=r: score(best | {r.key: c}))
        best_score = score(best)
    return best, float(best_score or 0)


def _narrower(r: Radio, width: int) -> Radio:
    return Radio(
        r.device, r.radio, r.band, r.channel, width, r.neighbours, r.busy, r.allowed, r.hears, r.htmode
    )


def plan(radios: list[Radio], dfs: bool = False, names: dict[str, str] | None = None) -> dict[str, Any]:
    """Channels for our radios, band by band (see the top). On 5 GHz, when our access points
    can't be kept apart at their width, 40 MHz is tried for the ones that can change it."""
    names = names or {}
    hints: list[str] = []
    out: list[Suggestion] = []
    totals = {"before": 0.0, "after": 0.0}
    for band in ("2g", "5g"):
        mine = [r for r in radios if r.band == band]
        if not mine:
            continue
        by_key = {r.key: r for r in radios}
        found = _assign(mine, by_key, dfs)
        if found is None:
            continue
        best, best_score = found
        if band == "5g" and any(r.width > 40 and r.htmode for r in mine):
            narrow = [_narrower(r, 40) if r.width > 40 and r.htmode else r for r in mine]
            narrow_keys = by_key | {r.key: r for r in narrow}
            other = _assign(narrow, narrow_keys, dfs)
            if other is not None and other[1] < 0.7 * best_score:
                mine, by_key, (best, best_score) = narrow, narrow_keys, other
        current = {r.key: r.channel for r in mine if r.channel}
        spans = [span(best[r.key], 20 if band == "2g" else r.width, band) for r in mine]
        crowded = any(overlap(a, b) for a, b in itertools.combinations(spans, 2))
        if crowded and band == "5g" and not dfs:
            hints.append(
                "5 GHz without radar channels has too little room to keep your access points apart:"
                " allowing radar channels (DFS) gives them more."
            )
        elif crowded:
            hints.append(f"{'2.4' if band == '2g' else '5'} GHz: more access points than channels apart.")
        for r in mine:
            was = next(x for x in radios if x.key == r.key)  # as it is now
            ch = best[r.key]
            after, why = cost(r, ch, best, by_key, names)
            before = (
                cost(was, was.channel, current, {x.key: x for x in radios}, names)[0]
                if was.channel
                else after
            )
            totals["before"] += before
            totals["after"] += after
            htmode = None
            prefix = (re.match(r"[A-Z]+", r.htmode or "") or [None])[0] or "HE"
            if band == "2g" and r.width > 20:
                htmode = f"{prefix}20"
                why.insert(0, "2.4 GHz wider than 20 MHz overlaps most of the band: 20 MHz")
            elif r.width != was.width:
                htmode = f"{prefix}{r.width}"
                why.insert(0, f"{r.width} MHz instead of {was.width}, so your access points don't overlap")
            if was.channel == ch and htmode is None:
                reason = "stays: " + ("; ".join(why) if why else "nothing in the way")
            elif was.channel is None:
                reason = f"fixed instead of automatic: {'; '.join(why) or 'nothing in the way'}"
            else:
                now_why = "; ".join(cost(was, was.channel, current, {x.key: x for x in radios}, names)[1])
                then = "; ".join(why) or "nothing in the way"
                reason = f"now on {was.channel}: {now_why or '—'}. On {ch}: {then}"
            if not r.neighbours and not r.busy:
                reason += " (not scanned: kept apart from your access points only)"
            out.append(
                Suggestion(
                    r.device,
                    r.radio,
                    band,
                    was.channel,
                    ch,
                    20 if band == "2g" else r.width,
                    htmode,
                    reason,
                    round(before, 1),
                    round(after, 1),
                )
            )
    return {
        "radios": [s.view() for s in out],
        "before": round(totals["before"], 1),
        "after": round(totals["after"], 1),
        "dfs": dfs,
        "hints": hints,
    }


def radios_from(
    settings: dict[str, dict[str, Any]], scans: list[dict[str, Any]], fritz: dict[str, list[dict[str, Any]]]
) -> list[Radio]:
    """Our radios from the OpenWrt devices' wireless settings (radio section: band, channel,
    htmode), the FRITZ!Boxes' networks, and the newest scan of each radio."""
    newest: dict[str, dict[str, Any]] = {}
    for s in scans:
        newest[f"{s['device']}/{s['radio']}"] = s
    own_bssids: dict[str, str] = {}
    for s in newest.values():
        for b in s.get("bssids") or []:
            own_bssids[b.lower()] = f"{s['device']}/{s['radio']}"
    out: list[Radio] = []
    for device, values in settings.items():
        for r in values.get("radios") or []:
            band = r.get("band") or ""
            if band not in ("2g", "5g"):
                continue
            ch = r.get("channel")
            scan = newest.get(f"{device}/{r['section']}") or {}
            radio = Radio(
                device,
                r["section"],
                band,
                int(ch) if str(ch).isdigit() else None,
                width_of(r.get("htmode"), band),
                htmode=r.get("htmode"),
                allowed=[
                    f["channel"]
                    for f in scan.get("freqs") or []
                    if f.get("channel") and not f.get("restricted")
                ]
                if scan.get("freqs")
                else None,
            )
            for n in scan.get("neighbours") or []:
                mine = own_bssids.get(str(n.get("bssid") or "").lower())
                if mine and mine != radio.key:
                    radio.hears[mine] = max(radio.hears.get(mine, -200), n.get("signal") or -100)
                elif not mine:
                    radio.neighbours.append(n)
            radio.busy = {
                _channel_of(int(x["mhz"])): int(x["busy"])
                for x in scan.get("survey") or []
                if x.get("mhz") and x.get("busy") is not None
            }
            out.append(radio)
    for device, networks in fritz.items():
        seen: set[str] = set()
        for n in networks:
            if n.get("guest") or n.get("band") not in ("2g", "5g") or n["band"] in seen:
                continue
            seen.add(n["band"])
            out.append(
                Radio(
                    device,
                    f"wlan{n['index']}",
                    n["band"],
                    n.get("channel") or None,
                    20 if n["band"] == "2g" else 80,
                    allowed=[c for c in n.get("possible_channels") or [] if c] or None,
                )
            )
    return out


def _channel_of(mhz: int) -> int:
    if 2412 <= mhz <= 2472:
        return (mhz - 2407) // 5
    if mhz == 2484:
        return 14
    return (mhz - 5000) // 5


class Scans:
    """Scans kept over time."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._lock = threading.Lock()

    def add(self, entries: list[dict[str, Any]]) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(self.folder / "scans.jsonl", "a") as f:
                for e in entries:
                    f.write(json.dumps(e, separators=(",", ":")) + "\n")
            self._prune()

    def all(self, since: float | None = None) -> list[dict[str, Any]]:
        out = []
        with contextlib.suppress(OSError):
            for ln in (self.folder / "scans.jsonl").read_text().splitlines():
                with contextlib.suppress(ValueError):
                    e = json.loads(ln)
                    if since is None or e.get("t", 0) >= since:
                        out.append(e)
        return out

    def _prune(self) -> None:
        keep = self.all(time.time() - KEEP_SCANS)
        path = self.folder / "scans.jsonl"
        if path.stat().st_size > 20 * 2**20 or len(keep) < len(self.all()):
            tmp = path.with_name(f".scans.{os.getpid()}.tmp")
            tmp.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in keep[-5000:]))
            tmp.replace(path)

    def note_plan(self, applied: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.folder / "plans.jsonl", "a") as f:
            f.write(json.dumps(applied) + "\n")

    def plans(self) -> list[dict[str, Any]]:
        out = []
        with contextlib.suppress(OSError):
            for ln in (self.folder / "plans.jsonl").read_text().splitlines()[-20:]:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(ln))
        return out


def lineup(
    openwrt: dict[str, Any],
    fritz: dict[str, list[dict[str, Any]]],
    fingerprints: dict[tuple[str, int], str | None],
) -> list[dict[str, Any]]:
    """Every network by name, over OpenWrt devices (sync.survey's answer) and FRITZ!Boxes."""
    networks: dict[str, dict[str, Any]] = {}
    for n in openwrt.get("networks") or []:
        net = networks.setdefault(n["ssid"], {"ssid": n["ssid"], "on": []})
        for e in n["on"]:
            net["on"].append(e | {"kind": "openwrt", "guest": False})
    for device, nets in fritz.items():
        for n in nets:
            if not n.get("ssid"):
                continue
            net = networks.setdefault(n["ssid"], {"ssid": n["ssid"], "on": []})
            net["on"].append(
                {
                    "device": device,
                    "section": f"wlan{n['index']}",
                    "radio": f"wlan{n['index']}",
                    "band": n.get("band"),
                    "encryption": n.get("encryption"),
                    "key": fingerprints.get((device, n["index"])),
                    "disabled": not n.get("enabled"),
                    "kind": "fritzbox",
                    "guest": bool(n.get("guest")),
                    "clients": n.get("clients"),
                }
            )
    out = []
    for net in networks.values():
        on = net["on"]
        keys = {e["key"] for e in on if e.get("key")}
        net |= {
            "devices": sorted({e["device"] for e in on}),
            "bands": sorted({e["band"] for e in on if e.get("band")}),
            "same_key": len(keys) == 1 if all(e.get("key") for e in on) else None,
            "same_encryption": len({_enc(e.get("encryption")) for e in on}) <= 1,
            "off": sorted({e["device"] for e in on if e.get("disabled")}),
            "guest": any(e.get("guest") for e in on),
        }
        out.append(net)
    return sorted(out, key=lambda n: (n["guest"], -len(n["devices"]), n["ssid"]))


def _enc(e: str | None) -> str:
    """Encryption, the same words for OpenWrt and FRITZ!Box."""
    return {
        "psk2": "WPA2",
        "psk2+ccmp": "WPA2",
        "11i": "WPA2",
        "sae": "WPA3",
        "WPA3": "WPA3",
        "sae-mixed": "WPA2/WPA3",
        "11iandWPA3": "WPA2/WPA3",
        "psk-mixed": "WPA/WPA2",
        "WPAand11i": "WPA/WPA2",
        "none": "open",
        "None": "open",
        "owe": "OWE",
    }.get(e or "", e or "?")
