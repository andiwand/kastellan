"""Alarms over HTTP: the open ones, acknowledging, muting a device, a test notification."""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import notify
from .alarms import Message, Severity
from .auth_api import client_address, require_admin

if TYPE_CHECKING:
    from .hub import Hub


class Ack(BaseModel):
    note: str = Field(default="", max_length=500)


class Mute(BaseModel):
    minutes: int = Field(ge=0, le=60 * 24 * 30)  # 0 unmutes


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.get("/alarms")
    def alarms() -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "open": [asdict(a) for a in hub.alarms.open.values()],
            "muted": {k: v for k, v in hub.alarms.muted.items() if v > now},
            "channels": len(hub.config.notify),
            "limits": {
                "disk_warning": hub.config.alarms.disk_warning,
                "disk_critical": hub.config.alarms.disk_critical,
            },
            # The dead man's switch: on (a URL is set), when it was last reached, and why not.
            "heartbeat": {
                "on": bool(hub.config.heartbeat),
                "last": hub.heartbeat_ok,
                "error": hub.heartbeat_error,
            },
        }

    @api.post("/alarms/{key:path}/ack")
    def acknowledge(key: str, body: Ack, request: Request) -> dict[str, Any]:
        w = require_admin(request)
        alarm = hub.alarms.acknowledge(key, w.user, body.note, datetime.now(UTC))
        if alarm is None:
            raise HTTPException(404, "no such open alarm")
        hub.audit.record("alarm.ack", w.user, client_address(request.scope), alarm=key, note=body.note)
        return asdict(alarm)

    @api.post("/devices/{id}/mute")
    def mute(id: str, body: Mute, request: Request) -> dict[str, Any]:
        w = require_admin(request)
        if id not in hub.config.devices:
            raise HTTPException(404, "no such device")
        until = datetime.now(UTC) + timedelta(minutes=body.minutes) if body.minutes else None
        hub.alarms.mute(id, until)
        hub.audit.record(
            "device.mute", w.user, client_address(request.scope), device=id, minutes=body.minutes
        )
        return {"device": id, "muted_until": until}

    last_test = [0.0]

    @api.post("/notify/test")
    async def test(request: Request) -> dict[str, int]:
        w = require_admin(request)
        if time.monotonic() - last_test[0] < 60:
            raise HTTPException(429, "one test a minute")
        last_test[0] = time.monotonic()
        if not hub.config.notify:
            raise HTTPException(400, "no notify: channels in config.yaml")
        message = Message(Severity.CRITICAL, "Kastellan test", f"sent by {w.user or 'an admin'}")
        failed = await notify.send(hub.config.notify, [message], datetime.now(UTC), push=hub.push)
        hub.audit.record("notify.test", w.user, client_address(request.scope))
        if failed:
            raise HTTPException(502, f"{len(failed)} of {len(hub.config.notify)} channels failed")
        return {"channels": len(hub.config.notify)}

    return api
