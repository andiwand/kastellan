"""The internet connection: how it is now, how it has been, and how fast it is.

Every `internet.every` seconds the hub looks, from where it is:
- the router (its address answers ping),
- the line, as the router tells it (a FRITZ!Box's connection state, an OpenWrt WAN interface),
- a few outside addresses of different providers, IPv4 and (when the hub has IPv6) IPv6: loss,
  latency and jitter over all of them (one provider's trouble isn't an outage),
- name resolution.
An outage is placed at the first step that fails: router → line → internet → DNS; the alarm says
which. Each look is a sample (`<data>/internet/<day>.jsonl`, kept 31 days); every day is summed
up for good (`days.json`: availability, outage time, latency), outages and the line's events
(reconnects, a new public address) are kept as lists.

Bandwidth in use comes from the router's counters (nothing is added to the line). Speed tests
are measured transfers, off by default (`internet.speedtest`): on a schedule, never while the
line is busy, and on request; latency under load (bufferbloat) is measured alongside.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import socket
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from icmplib import async_ping
from icmplib.exceptions import ICMPLibError, SocketPermissionError

KEEP_SAMPLES_DAYS = 31
KEEP_DAYS = 3 * 366
STEPS = {
    "router": "the router doesn't answer",
    "line": "the router has no internet connection",
    "provider": "the provider's first routers don't answer (its network)",
    "internet": "nothing outside answers (beyond the provider)",
    "dns": "names can't be resolved",
}
RECONNECTS = 3  # in 24 hours: a warning
ERRORED_PER_HOUR = 60  # DSL errored seconds
LOW_MARGIN = 60  # 0.1 dB: 6 dB downstream noise margin
DEGRADED_LOSS = 0.1
DEGRADED_MS = 150.0


@dataclass
class Sample:
    t: float
    state: str  # up, degraded, down, unknown
    step: str | None = None  # where it fails (STEPS)
    router_ms: float | None = None
    line: bool | None = None  # the router says it is connected
    loss: float | None = None  # 0..1 over all outside targets (IPv4)
    ms: float | None = None  # median round trip
    jitter: float | None = None
    v6_loss: float | None = None
    v6_ms: float | None = None
    dns_ms: float | None = None
    hop_ms: float | None = None  # the provider's first router(s) that answer pings
    hop_loss: float | None = None
    down: float | None = None  # bit/s through the line now (router counters)
    up: float | None = None
    note: str | None = None


@dataclass
class Probe:
    """What one look needs from the hub."""

    router: str | None  # its address
    line: bool | None  # what the router says, None: it doesn't say
    rate: dict[str, float] | None  # {"down", "up"} bit/s from the router
    targets: list[str]
    targets_v6: list[str]  # empty: the hub has no IPv6
    names: list[str]
    hops: list[str] = field(default_factory=list)  # the provider's first routers (that answer)


async def _pings(address: str, count: int = 4) -> tuple[int, int, list[float]]:
    """(sent, received, round trips in ms)."""
    try:
        r = await async_ping(address, count=count, interval=0.2, timeout=1.5, privileged=False)
    except SocketPermissionError:
        raise
    except (ICMPLibError, OSError):
        return count, 0, []
    return r.packets_sent, r.packets_received, list(r.rtts)


def _summary(
    results: list[tuple[int, int, list[float]]],
) -> tuple[float | None, float | None, float | None, bool]:
    """(loss, median ms, jitter ms, anything answered)."""
    sent = sum(r[0] for r in results)
    got = sum(r[1] for r in results)
    rtts = [x for r in results for x in r[2]]
    if not sent:
        return None, None, None, False
    deltas = [abs(a - b) for _, _, times in results for a, b in itertools.pairwise(times)]
    jitter = statistics.mean(deltas) if deltas else None
    return (
        round(1 - got / sent, 3),
        round(statistics.median(rtts), 2) if rtts else None,
        round(jitter, 2) if jitter is not None else None,
        got > 0,
    )


async def _resolve(name: str) -> float | None:
    """ms to resolve a name through the hub's resolvers; None: it failed."""
    loop = asyncio.get_running_loop()
    start = time.monotonic()
    try:
        await asyncio.wait_for(loop.getaddrinfo(name, 443, type=socket.SOCK_STREAM), 5)
    except Exception:
        return None
    return round((time.monotonic() - start) * 1000, 1)


async def look(p: Probe, now: float | None = None) -> Sample:
    """One look at the connection."""
    now = time.time() if now is None else now
    try:
        router, v4, v6, dns, hops = await asyncio.gather(
            _pings(p.router, 3) if p.router else asyncio.sleep(0, None),
            asyncio.gather(*(_pings(t) for t in p.targets)),
            asyncio.gather(*(_pings(t) for t in p.targets_v6)),
            asyncio.gather(*(_resolve(n) for n in p.names)),
            asyncio.gather(*(_pings(h, 3) for h in p.hops)),
        )
    except SocketPermissionError:
        return Sample(now, "unknown", note="the hub may not ping (see README)")
    s = Sample(now, "up", line=p.line)
    if p.rate:
        s.down, s.up = p.rate.get("down"), p.rate.get("up")
    router_ok = None
    if router is not None:
        router_ok = router[1] > 0
        s.router_ms = round(statistics.median(router[2]), 2) if router[2] else None
    s.loss, s.ms, s.jitter, v4_ok = _summary(list(v4))
    hop_ok = None
    if p.hops:
        s.hop_loss, s.hop_ms, _, hop_ok = _summary(list(hops))
    v6_ok = False
    if p.targets_v6:
        s.v6_loss, s.v6_ms, _, v6_ok = _summary(list(v6))
    answered = [d for d in dns if d is not None]
    dns_ok = bool(answered) if p.names else True
    s.dns_ms = min(answered) if answered else None
    outside_ok = v4_ok or v6_ok
    if not outside_ok and router_ok is False:
        s.state, s.step = "down", "router"
    elif not outside_ok and p.line is False:
        s.state, s.step = "down", "line"
    elif not outside_ok and hop_ok is False:
        s.state, s.step = "down", "provider"
    elif not v4_ok and not v6_ok:
        s.state, s.step = "down", "internet"
    elif not dns_ok:
        s.state, s.step = "down", "dns"
    elif (
        (s.loss or 0) > DEGRADED_LOSS
        or (s.ms or 0) > DEGRADED_MS
        or (p.targets_v6 and (s.v6_loss or 0) > DEGRADED_LOSS)
    ):
        s.state = "degraded"
    return s


def provider_hops(route: list[dict[str, Any]], router: str | None) -> list[str]:
    """From a route outward, the provider's first routers: after the home network (private
    addresses, the router), the first two that answered. CGNAT (100.64/10) counts as the
    provider's."""
    import ipaddress

    out = []
    for hop in route:
        address = hop.get("address")
        try:
            a = ipaddress.ip_address(address)
        except (TypeError, ValueError):
            continue
        cgnat = a.version == 4 and a in ipaddress.ip_network("100.64.0.0/10")
        if address == router or (a.is_private and not cgnat) or a.is_loopback:
            continue
        out.append(str(a))
        if len(out) == 2:
            break
    return out


async def route(target: str, max_hops: int = 8) -> list[dict[str, Any]]:
    """The route to `target`; icmplib needs a raw socket, so return [] without permission."""
    from icmplib import traceroute

    def run() -> list[dict[str, Any]]:
        hops = traceroute(target, count=2, interval=0.05, timeout=1, max_hops=max_hops)
        return [{"distance": h.distance, "address": h.address, "ms": round(h.avg_rtt, 2)} for h in hops]

    try:
        return await asyncio.to_thread(run)
    except (ICMPLibError, OSError):
        pass
    return []


def line_from(
    report: dict[str, Any] | None, fritz: dict[str, Any] | None
) -> tuple[bool | None, dict[str, Any]]:
    """Whether the router says the line is up, and what else it says about it (public address,
    connected for, sync rates): from a FRITZ!Box's report or an OpenWrt device's WAN."""
    if fritz:
        net = fritz.get("internet") or {}
        conn = net.get("connection")
        info = {
            "address": net.get("address"),
            "address_v6": net.get("address_v6"),
            "connected_for": net.get("connected_for"),
            "access": net.get("access"),
            "dsl": net.get("dsl"),
            "max_down": net.get("max_down"),
            "max_up": net.get("max_up"),
        }
        return (conn == "Connected") if conn else None, info
    if report:
        for i in report.get("interfaces") or []:
            if str(i.get("name") or "").startswith("wan") and i.get("name") != "wan6":
                return bool(i.get("up")), {
                    "address": (i.get("ipv4") or [None])[0],
                    "connected_for": i.get("uptime"),
                    "access": i.get("proto"),
                    "wan_device": i.get("device"),
                }
    return None, {}


# --- Keeping it -------------------------------------------------------------------------------


def _day(t: float) -> str:
    return datetime.fromtimestamp(t).astimezone().date().isoformat()


@dataclass
class Outage:
    start: float
    end: float | None
    step: str | None

    def view(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "seconds": (self.end or time.time()) - self.start,
            "step": self.step,
            "why": STEPS.get(self.step or "", "down"),
        }


@dataclass
class State:
    outage: Outage | None = None
    down_since: float | None = None  # down samples in a row since
    last_address: str | None = None
    last_connected_for: float | None = None
    days: dict[str, dict[str, Any]] = field(default_factory=dict)


class Internet:
    """The samples, the days, outages, events and speed tests, in <data>/internet/."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.latest: Sample | None = None
        self.state = State()
        self.line: dict[str, Any] = {}  # what the router says now
        self.router: str | None = None
        self.testing = False
        self.speed_error: str | None = None
        self.speed_tried = 0.0
        # The way out (traceroute, hourly) and the provider's first routers that answer pings.
        self.route: list[dict[str, Any]] = []
        self.hops: list[str] = []
        self.route_checked = 0.0
        # The line's error counters over the last hours (t, crc, errored seconds): their growth.
        self.errors: list[tuple[float, int | None, int | None]] = []
        self._load()

    def _path(self, name: str) -> Path:
        return self.folder / name

    def _load(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(self._path("state.json").read_text())
            outage = data.get("outage")
            self.state = State(
                outage=Outage(**outage) if outage else None,
                down_since=data.get("down_since"),
                last_address=data.get("last_address"),
                last_connected_for=data.get("last_connected_for"),
                days=data.get("days") or {},
            )

    def _save_state(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {
            "outage": asdict(self.state.outage) if self.state.outage else None,
            "down_since": self.state.down_since,
            "last_address": self.state.last_address,
            "last_connected_for": self.state.last_connected_for,
            "days": self.state.days,
        }
        tmp = self._path(f".state.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self._path("state.json"))

    def _append(self, name: str, entry: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self._path(name), "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def _read(self, name: str, limit: int = 500) -> list[dict[str, Any]]:
        try:
            lines = self._path(name).read_text().splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for ln in lines:
            with contextlib.suppress(ValueError):
                out.append(json.loads(ln))
        return out

    def note(self, s: Sample, line: dict[str, Any], interval: float) -> list[dict[str, Any]]:
        """Keep a sample; what happened with it (outage start/end, reconnect, new address)."""
        happened: list[dict[str, Any]] = []
        self.latest, self.line = s, line
        self._append(f"{_day(s.t)}.jsonl", {k: v for k, v in asdict(s).items() if v is not None})
        # The day's sums (seconds in each state, by the interval each sample stands for).
        day = self.state.days.setdefault(
            _day(s.t), {"up": 0, "degraded": 0, "down": 0, "unknown": 0, "ms": []}
        )
        day[s.state] = day.get(s.state, 0) + interval
        if s.ms is not None:
            day["ms"] = [*(day.get("ms") or [])[-8640:], s.ms]
        for d, v in self.state.days.items():  # a past day keeps only its median
            if d != _day(s.t) and isinstance(v.get("ms"), list):
                v["ms"] = round(statistics.median(v["ms"]), 1) if v["ms"] else None
        # Outages: from the first down sample to the first that isn't.
        st = self.state
        if s.state == "down":
            if st.outage is None:
                st.outage = Outage(s.t, None, s.step)
                happened.append({"t": s.t, "kind": "outage", "text": f"down: {STEPS.get(s.step or '', '')}"})
        elif s.state != "unknown" and st.outage is not None:
            st.outage.end = s.t
            self._append("outages.jsonl", asdict(st.outage))
            happened.append(
                {"t": s.t, "kind": "back", "text": f"back after {_duration(s.t - st.outage.start)}"}
            )
            st.outage = None
        st.down_since = (st.down_since or s.t) if s.state == "down" else None
        # The line's own events, as the router tells them.
        address = line.get("address")
        if address and st.last_address and address != st.last_address:
            happened.append({"t": s.t, "kind": "address", "text": f"new public address {address}"})
        if address:
            st.last_address = address
        connected = line.get("connected_for")
        if (
            connected is not None
            and st.last_connected_for is not None
            and connected + interval < st.last_connected_for
        ):
            happened.append({"t": s.t, "kind": "reconnect", "text": "the router connected again"})
        if connected is not None:
            st.last_connected_for = connected
        for e in happened:
            self._append("events.jsonl", e)
        self._save_state()
        return happened

    def prune(self, today: date | None = None) -> None:
        today = today or datetime.now().astimezone().date()
        for path in self.folder.glob("????-??-??.jsonl"):
            with contextlib.suppress(ValueError, OSError):
                if (today - date.fromisoformat(path.stem)).days > KEEP_SAMPLES_DAYS:
                    path.unlink()
        cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.state.days = {d: v for d, v in self.state.days.items() if d >= cut}
        for name in ("outages.jsonl", "events.jsonl", "speedtests.jsonl"):
            entries = self._read(name, 5000)
            if len(entries) >= 5000:
                self._path(name).write_text("".join(json.dumps(e) + "\n" for e in entries[-2000:]))

    def samples(self, since: float, until: float | None = None) -> list[dict[str, Any]]:
        until = until or time.time()
        out = []
        day = datetime.fromtimestamp(since).astimezone().date()
        last = datetime.fromtimestamp(until).astimezone().date()
        while day <= last:
            for e in self._read(f"{day.isoformat()}.jsonl", 100_000):
                if since <= e.get("t", 0) <= until:
                    out.append(e)
            day += timedelta(days=1)
        return out

    def series(self, hours: float, buckets: int) -> list[dict[str, Any]]:
        """Samples over the last `hours` in `buckets` slices: median ms, mean loss, worst state,
        mean rates."""
        now = time.time()
        start = now - hours * 3600
        step = hours * 3600 / buckets
        slots: list[list[dict[str, Any]]] = [[] for _ in range(buckets)]
        for e in self.samples(start, now):
            i = min(buckets - 1, int((e["t"] - start) / step))
            slots[i].append(e)
        order = {"unknown": 0, "up": 1, "degraded": 2, "down": 3}
        out = []
        for i, slot in enumerate(slots):
            ms = [e["ms"] for e in slot if e.get("ms") is not None]
            loss = [e["loss"] for e in slot if e.get("loss") is not None]
            down = [e["down"] for e in slot if e.get("down") is not None]
            up = [e["up"] for e in slot if e.get("up") is not None]
            out.append(
                {
                    "t": start + i * step,
                    "state": max((e["state"] for e in slot), key=lambda x: order.get(x, 0)) if slot else None,
                    "ms": round(statistics.median(ms), 1) if ms else None,
                    "loss": round(statistics.mean(loss), 3) if loss else None,
                    "down": round(statistics.mean(down)) if down else None,
                    "up": round(statistics.mean(up)) if up else None,
                }
            )
        return out

    def days(self, n: int = 62) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.state.days)[-n:]:
            v = self.state.days[d]
            known = v.get("up", 0) + v.get("degraded", 0) + v.get("down", 0)
            out.append(
                {
                    "day": d,
                    "availability": round(1 - v.get("down", 0) / known, 5) if known else None,
                    "down_seconds": round(v.get("down", 0)),
                    "degraded_seconds": round(v.get("degraded", 0)),
                    "ms": _median(v.get("ms")),
                }
            )
        return out

    def months(self) -> list[dict[str, Any]]:
        sums: dict[str, list[float]] = {}
        for d, v in self.state.days.items():
            m = sums.setdefault(d[:7], [0.0, 0.0])
            m[0] += v.get("down", 0)
            m[1] += v.get("up", 0) + v.get("degraded", 0) + v.get("down", 0)
        return [
            {
                "month": m,
                "availability": round(1 - down / known, 5) if known else None,
                "down_seconds": round(down),
            }
            for m, (down, known) in sorted(sums.items())
        ][-36:]

    def outages(self, n: int = 50) -> list[dict[str, Any]]:
        out = [Outage(**e).view() for e in self._read("outages.jsonl", n)]
        if self.state.outage:
            out.append(self.state.outage.view())
        return list(reversed(out))[:n]

    def note_errors(self, t: float, dsl: dict[str, Any] | None) -> None:
        """The DSL error counters now; a counter that went back (a resync) starts over."""
        if not dsl:
            return
        crc, es = dsl.get("crc"), dsl.get("errored_seconds")
        if self.errors:
            _, crc0, es0 = self.errors[-1]
            if (crc is not None and crc0 is not None and crc < crc0) or (
                es is not None and es0 is not None and es < es0
            ):
                self.errors = []
        self.errors.append((t, crc, es))
        self.errors = [e for e in self.errors if t - e[0] <= 3 * 3600]

    def trouble(self, now: float, dsl: dict[str, Any] | None) -> list[tuple[str, str, str]]:
        """What is wrong with the line itself (key, title, detail): reconnects, errors, little
        noise margin. Warnings: the line still works, but not well."""
        out = []
        reconnects = [
            e for e in self.events(200) if e.get("kind") == "reconnect" and now - e.get("t", 0) < 86400
        ]
        if len(reconnects) >= RECONNECTS:
            out.append(
                (
                    "internet:reconnects",
                    f"The line reconnected {len(reconnects)} times in 24 h",
                    "the router's connection dropped and came back",
                )
            )
        hour = [e for e in self.errors if now - e[0] <= 3600]
        if len(hour) >= 2 and hour[-1][0] - hour[0][0] >= 900:
            first, last = hour[0], hour[-1]
            span = (last[0] - first[0]) / 3600
            if (
                first[2] is not None
                and last[2] is not None
                and (last[2] - first[2]) / span > ERRORED_PER_HOUR
            ):
                out.append(
                    (
                        "internet:errors",
                        "The line has many errors",
                        f"{round((last[2] - first[2]) / span)} errored seconds an hour (DSL)",
                    )
                )
        margin = (dsl or {}).get("snr_down")
        if isinstance(margin, int) and 0 < margin < LOW_MARGIN:
            out.append(
                (
                    "internet:margin",
                    "The line has little noise margin",
                    f"{margin / 10:g} dB downstream: it may drop or sync slower",
                )
            )
        return out

    def events(self, n: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._read("events.jsonl", n)))

    def speedtests(self, n: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._read("speedtests.jsonl", n)))

    def note_speedtest(self, result: dict[str, Any]) -> None:
        self._append("speedtests.jsonl", result)


def _median(ms: Any) -> float | None:
    if isinstance(ms, list):
        return round(statistics.median(ms), 1) if ms else None
    return ms


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


# --- Speed tests ------------------------------------------------------------------------------

MAX_TEST = 15.0  # seconds a direction may take at most


async def speedtest(
    download: str,
    upload: str | None,
    upload_bytes: int,
    ping_target: str,
    client: httpx.AsyncClient | None = None,
    iperf3: str | None = None,
) -> dict[str, Any]:
    """Measure download and upload (bit/s) and latency idle and under load (ms): by HTTP
    transfers, or against an iperf3 server."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=30, follow_redirects=True)
    try:
        idle = await _latency(ping_target, 5)
        loaded: list[float] = []
        stop = asyncio.Event()
        watcher = asyncio.create_task(_watch(ping_target, loaded, stop))
        try:
            if iperf3:
                down = await _iperf3(iperf3, reverse=True)
                up = await _iperf3(iperf3, reverse=False)
            else:
                down = await _download(client, download)
                up = await _upload(client, upload, upload_bytes) if upload else None
        finally:
            stop.set()
            await watcher
    finally:
        if own:
            await client.aclose()
    return {
        "t": time.time(),
        "down": down,
        "up": up,
        "ms_idle": idle,
        "ms_loaded": round(statistics.median(loaded), 1) if loaded else None,
        "server": iperf3 or httpx.URL(download).host,
        "how": "iperf3" if iperf3 else "http",
    }


def iperf3_rate(output: str) -> float:
    """bit/s from iperf3's JSON (-J): what the receiving side got."""
    data = json.loads(output)
    if data.get("error"):
        raise RuntimeError(f"iperf3: {str(data['error'])[:200]}")
    end = data.get("end") or {}
    part = end.get("sum_received") or end.get("sum") or {}
    rate = part.get("bits_per_second")
    if not isinstance(rate, int | float) or rate <= 0:
        raise RuntimeError("iperf3 gave no rate")
    return round(rate)


async def _iperf3(server: str, reverse: bool) -> float:
    """One direction against an iperf3 server (reverse: it sends, the hub receives)."""
    import shutil

    exe = shutil.which("iperf3")
    if exe is None:
        raise RuntimeError("iperf3 isn't installed where the hub runs")
    host, port = server, "5201"
    if server.startswith("["):
        host, _, rest = server[1:].partition("]")
        port = rest.lstrip(":") or port
    elif server.count(":") == 1:
        host, port = server.split(":")
    args = [exe, "-c", host, "-p", port, "-J", "-t", "8", "-P", "4", *(["-R"] if reverse else [])]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
    except TimeoutError:
        raise RuntimeError("iperf3 took too long") from None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    return iperf3_rate(out.decode(errors="replace"))


async def _latency(target: str, count: int) -> float | None:
    with contextlib.suppress(Exception):
        _sent, _got, rtts = await _pings(target, count)
        if rtts:
            return round(statistics.median(rtts), 1)
    return None


async def _watch(target: str, into: list[float], stop: asyncio.Event) -> None:
    while not stop.is_set():
        with contextlib.suppress(Exception):
            _, _, rtts = await _pings(target, 1)
            into.extend(rtts)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), 0.4)


async def _download(client: httpx.AsyncClient, url: str) -> float:
    start = time.monotonic()
    got = 0
    try:
        async with asyncio.timeout(MAX_TEST), client.stream("GET", url) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                got += len(chunk)
    except TimeoutError:
        if not got:
            raise
    seconds = max(time.monotonic() - start, 0.001)
    return round(got * 8 / seconds)


async def _upload(client: httpx.AsyncClient, url: str, size: int) -> float:
    block = os.urandom(64 * 1024)
    sent = 0
    start = time.monotonic()

    async def body():
        nonlocal sent
        while sent < size and time.monotonic() - start < MAX_TEST:
            chunk = block[: size - sent]
            sent += len(chunk)
            yield chunk

    async with asyncio.timeout(MAX_TEST):
        r = await client.post(url, content=body(), headers={"Content-Type": "application/octet-stream"})
        r.raise_for_status()
    return round(sent * 8 / max(time.monotonic() - start, 0.001))


def slow(tests: list[dict[str, Any]], booked: float | None, now: float) -> bool:
    """The last three tests (within three days) all below half the booked download speed."""
    if not booked:
        return False
    recent = [t for t in tests[:3] if now - t.get("t", 0) < 3 * 86400 and t.get("down")]
    return len(recent) == 3 and all(t["down"] < booked * 1e6 / 2 for t in recent)
