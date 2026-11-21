"""Web push over HTTP (push.py): each signed-in person turns it on for their own browsers.
Observers too: they read alarms like admins, and change nothing but their own subscriptions."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth_api import client_address, who

if TYPE_CHECKING:
    from .hub import Hub


class Keys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: Keys
    label: str = Field(default="", max_length=80)  # which browser, for the list


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=1000)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/push")
    last_test: dict[str, float] = {}

    def user(request: Request) -> str:
        w = who(request)
        if w.user is None:
            raise HTTPException(403, "push is for signed-in people (turn on sign-in first)")
        return w.user

    @api.get("")
    def mine(request: Request) -> dict[str, Any]:
        name = user(request)
        return {
            "public_key": hub.push.public_key,
            "subscriptions": [
                {"endpoint": s["endpoint"], "label": s["label"], "created": s["created"]}
                for s in hub.push.of(name)
            ],
            # Whether a push: channel reaches this person at all.
            "channel": any(c.push in (name, "*") for c in hub.config.notify),
        }

    @api.post("/subscribe")
    def subscribe(body: Subscription, request: Request) -> dict[str, bool]:
        name = user(request)
        try:
            hub.push.add(name, body.endpoint, body.keys.p256dh, body.keys.auth, body.label)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        hub.audit.record("push.subscribe", name, client_address(request.scope), browser=body.label)
        return {"subscribed": True}

    @api.post("/unsubscribe")
    def unsubscribe(body: Endpoint, request: Request) -> dict[str, bool]:
        name = user(request)
        removed = hub.push.remove(name, body.endpoint)
        if removed:
            hub.audit.record("push.unsubscribe", name, client_address(request.scope))
        return {"removed": removed}

    @api.post("/test")
    async def test(request: Request) -> dict[str, int]:
        name = user(request)
        if time.monotonic() - last_test.get(name, -60) < 60:
            raise HTTPException(429, "one test a minute")
        last_test[name] = time.monotonic()
        mine = hub.push.of(name)
        if not mine:
            raise HTTPException(400, "no browser of yours has push on")
        payload = {
            "title": "Kastellan test",
            "body": f"Push works ({datetime.now(UTC):%H:%M} UTC).",
            "url": "/",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            ok = await hub.push.send(client, name, payload, retry=False)
        if not ok:
            raise HTTPException(502, "a push service didn't take it; try again later")
        return {"browsers": len(mine)}

    return api
