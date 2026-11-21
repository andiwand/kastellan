"""The internet connection over HTTP and as the hub's task (internet.py): how it is (everyone
signed in), a speed test now (admins)."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from . import internet
from .alarms import Problem, Severity
from .auth_api import client_address, require_admin
from .openwrt import client as ow

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


def router_of(hub: Hub) -> str | None:
    """The device that connects to the internet: as config.yaml says, else the one at the hub's
    gateway, else the only FRITZ!Box, else the only OpenWrt router."""
    cfg = hub.config
    if cfg.internet.router:
        return cfg.internet.router
    if hub.gateway:
        for id, d in cfg.devices.items():
            if d.host and (d.host == hub.gateway or hub.resolver.address(d.host) == hub.gateway):
                return id
    boxes = [id for id, d in cfg.devices.items() if d.fritzbox]
    if len(boxes) == 1:
        return boxes[0]
    routers = [id for id, d in cfg.devices.items() if d.openwrt and d.kind == "router"]
    return routers[0] if len(routers) == 1 else None


class Counters:
    """An OpenWrt router's WAN byte counters to bit/s, between two looks."""

    def __init__(self) -> None:
        self.last: tuple[float, dict[str, int]] | None = None

    def rate(self, now: float, counters: dict[str, int] | None) -> dict[str, float] | None:
        if counters is None:
            return None
        last, self.last = self.last, (now, counters)
        if last is None or now <= last[0]:
            return None
        span = now - last[0]
        rx, tx = counters["rx"] - last[1]["rx"], counters["tx"] - last[1]["tx"]
        if rx < 0 or tx < 0:  # counters started again (a reboot)
            return None
        return {"down": rx * 8 / span, "up": tx * 8 / span}


async def probe(hub: Hub, rid: str | None, counters: Counters) -> tuple[internet.Probe, dict[str, Any]]:
    cfg = hub.config.internet
    status = hub.monitor.status.get(rid) if rid else None
    device = hub.config.devices.get(rid) if rid else None
    now = datetime.now(UTC)

    def fresh(checked, error) -> bool:
        return checked is not None and not error and (now - checked).total_seconds() < 180

    router = status.router if status and fresh(status.router_checked, status.router_error) else None
    fritz = status.fritz if status and fresh(status.fritz_checked, status.fritz_error) else None
    line, info = internet.line_from(router, fritz)
    rate = None
    if fritz:
        r = (fritz.get("internet") or {}).get("rate") or {}
        if r.get("down") is not None:
            rate = {"down": r["down"], "up": r["up"]}
    elif device and device.openwrt and info.get("wan_device"):
        try:
            rate = counters.rate(time.time(), await ow.wan_counters(hub.ubus(rid), info["wan_device"]))
        except Exception:
            rate = None
    address = None
    if device and device.host:
        address = hub.resolver.address(device.host) or device.host
    elif hub.gateway:
        address = hub.gateway
    p = internet.Probe(
        router=address,
        line=line,
        rate=rate,
        targets=list(cfg.targets),
        targets_v6=list(cfg.targets_v6) if hub.own_v6 else [],
        names=list(cfg.dns),
    )
    return p, info


def busy(hub: Hub) -> bool:
    """The line carries enough now that a speed test would be wrong and in the way."""
    s = hub.internet.latest
    cfg = hub.config.internet.speedtest
    if s is None or time.time() - s.t > max(180, hub.config.internet.every * 3):
        return True  # do not start a scheduled load test on an unknown line
    return (
        s.state not in ("up", "degraded")
        or (s.down or 0) > (cfg.booked_down * 1e6 * 0.2 if cfg.booked_down else 5e6)
        or (s.up or 0) > (cfg.booked_up * 1e6 * 0.2 if cfg.booked_up else 1e6)
    )


async def run_speedtest(hub: Hub, by: str | None = None) -> dict[str, Any]:
    cfg = hub.config.internet.speedtest
    if hub.internet.testing:
        raise RuntimeError("a speed test is already running")
    hub.internet.testing = True
    hub.internet.speed_tried = time.time()
    try:
        result = await internet.speedtest(
            cfg.download, cfg.upload, cfg.upload_bytes, hub.config.internet.targets[0]
        )
        result["by"] = by
        hub.internet.note_speedtest(result)
        hub.internet.speed_error = None
        return result
    except Exception as e:
        hub.internet.speed_error = (str(e) or type(e).__name__)[:200]
        raise
    finally:
        hub.internet.testing = False


async def run(hub: Hub) -> None:
    """Look every `internet.every` seconds; speed tests when due."""
    counters = Counters()
    last_prune = 0.0
    last_router = None

    async def scheduled_test() -> None:
        try:
            await run_speedtest(hub)
        except Exception as e:
            log.info("speed test: %s", type(e).__name__)

    await asyncio.sleep(20)  # the router's first poll first
    while True:
        cfg = hub.config.internet
        if hub.demo:
            from . import demo_more

            hub.internet.router = "fritzbox"
            hub.internet.note(demo_more.sample(time.time()), demo_more.LINE, cfg.every)
        elif cfg.enabled:
            try:
                rid = router_of(hub)
                if rid != last_router:
                    counters, last_router = Counters(), rid
                hub.internet.router = rid
                p, info = await probe(hub, rid, counters)
                sample = await internet.look(p)
                await asyncio.to_thread(hub.internet.note, sample, info, cfg.every)
            except Exception:
                log.exception("looking at the internet connection failed")
        if not hub.demo:
            st = cfg.speedtest
            last = (hub.internet.speedtests(1) or [{}])[0].get("t", 0)
            last = max(last, hub.internet.speed_tried)
            if (
                cfg.enabled
                and st.enabled
                and not hub.internet.testing
                and time.time() - last > st.every_hours * 3600
                and not busy(hub)
            ):
                task = asyncio.create_task(scheduled_test())
                hub._background.add(task)
                task.add_done_callback(hub._background.discard)
            if time.time() - last_prune > 6 * 3600:
                last_prune = time.time()
                await asyncio.to_thread(hub.internet.prune)
        await asyncio.sleep(cfg.every)


def problems(hub: Hub) -> list[Problem]:
    """Down for two looks in a row: critical, on the router (saying which step fails); speed far
    below the booked one: a warning."""
    out: list[Problem] = []
    if not hub.config.internet.enabled:
        return out
    rid = hub.internet.router
    if rid is None or rid not in hub.config.devices:
        return out
    s, st = hub.internet.latest, hub.internet.state
    every = hub.config.internet.every
    label = hub.config.label(rid)
    if (
        s
        and time.time() - s.t < max(180, every * 3)
        and s.state == "down"
        and st.down_since is not None
        and s.t - st.down_since >= every
    ):
        why = internet.STEPS.get(s.step or "", "")
        out.append(
            Problem(
                "internet:down",
                Severity.CRITICAL,
                rid,
                "the internet connection",
                f"Internet is down: {why}",
                f"through {label}",
            )
        )
    booked = hub.config.internet.speedtest.booked_down
    if internet.slow(hub.internet.speedtests(3), booked, time.time()):
        out.append(
            Problem(
                "internet:slow",
                Severity.WARNING,
                rid,
                "the internet speed",
                f"Internet below half the booked {booked:g} Mbit/s",
                "the last three speed tests",
            )
        )
    return out


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/internet")
    ranges = {1: 60, 24: 96, 168: 84, 720: 90}

    @api.get("")
    async def view(hours: int = Query(default=24)) -> dict[str, Any]:
        if hours not in ranges:
            raise HTTPException(400, f"hours: one of {', '.join(map(str, ranges))}")
        net = hub.internet
        s = net.latest
        cfg = hub.config.internet
        return {
            "enabled": cfg.enabled,
            "router": net.router,
            "now": None if s is None else s.__dict__ | {"why": internet.STEPS.get(s.step or "")},
            "line": net.line,
            "outage": net.state.outage.view() if net.state.outage else None,
            "series": await asyncio.to_thread(net.series, hours, ranges[hours]),
            "days": net.days(62),
            "months": net.months(),
            "outages": await asyncio.to_thread(net.outages, 30),
            "events": await asyncio.to_thread(net.events, 30),
            "speedtests": await asyncio.to_thread(net.speedtests, 30),
            "speedtest": {
                "enabled": cfg.speedtest.enabled,
                "every_hours": cfg.speedtest.every_hours,
                "booked_down": cfg.speedtest.booked_down,
                "booked_up": cfg.speedtest.booked_up,
                "server": httpx.URL(cfg.speedtest.download).host,
                "testing": net.testing,
                "error": net.speed_error,
            },
            "targets": cfg.targets,
        }

    @api.post("/speedtest")
    async def test_now(request: Request) -> dict[str, Any]:
        """A speed test now (it uses the line for up to half a minute)."""
        w = require_admin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; nothing is measured")
        try:
            result = await run_speedtest(hub, by=w.user)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"the speed test failed: {hub.internet.speed_error}") from e
        hub.audit.record("internet.speedtest", w.user, client_address(request.scope))
        return result

    return api
