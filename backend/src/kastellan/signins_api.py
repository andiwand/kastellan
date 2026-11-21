"""The Access page over HTTP: sign-ins and attempts (signins.py) from the central logs and the
hub's audit log, per address with flags, and the hub's periodic look that tells a first success
from an outside address once (an info event)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request, Response

from . import signins
from .auth_api import require_admin, require_signin

if TYPE_CHECKING:
    from .hub import Hub

KNOWN = "signins:known-ok"  # outside address -> when it first succeeded (LogStore state)
NEW_FOR = 7 * 86400  # a first success counts as new this long
EVERY = 300


def compute(hub: Hub, hours: float) -> dict[str, Any]:
    """Blocking (the log store): the events and sources of the last `hours`."""
    now = time.time()
    since = now - hours * 3600
    lines = hub.logs.matching(since, signins.LIKES)
    events = [e for e in (signins.parse(ln) for ln in lines) if e is not None]
    events += signins.from_audit(hub.audit.recent(5000), since)
    own = {a for a in (hub.own_address,) if a}
    # Kastellan's own checks knock on SSH every few minutes: not attempts worth showing.
    events = [e for e in events if not (e.address in own and e.outcome == "attempt")]
    signins.mark_paths(events, dict(hub.vpn_addresses))
    known: dict[str, float] = hub.logs.get_state(KNOWN) or {}
    changed = False
    for e in events:
        if e.verified and e.outside and e.outcome == "ok" and e.address and e.address not in known:
            known[e.address] = e.ts
            changed = True
    if changed:
        hub.logs.set_state(KNOWN, known)
    old = {a for a, t in known.items() if t < now - NEW_FOR}
    srcs = signins.sources(events, old)
    events.sort(key=lambda e: e.ts, reverse=True)
    return {
        "hours": hours,
        "own": sorted(own),
        "summary": signins.summary(events),
        "sources": [s.view() for s in srcs[:500]],
        "events": [e.__dict__ | {"own": e.address in own} for e in events[:1000]],
        "total": len(events),
    }


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/signins")

    @api.get("")
    async def view(
        request: Request, response: Response, hours: float = Query(default=24, gt=0, le=24 * 31)
    ) -> dict[str, Any]:
        """Admins only: addresses and accounts."""
        require_admin(request)
        if not hub.demo:
            require_signin(request)
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(compute, hub, hours)

    return api


async def run(hub: Hub) -> None:
    """Every few minutes: a success from an outside address new in the last week is told once."""
    await asyncio.sleep(90)
    while True:
        try:
            out = await asyncio.to_thread(compute, hub, 1)
            hub.signin_flags = [s for s in out["sources"] if "new-outside" in s["flags"]]
        except Exception:  # the logs can't be read now: next time
            pass
        await asyncio.sleep(EVERY)
