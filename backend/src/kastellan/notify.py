"""Sending alarms: ntfy and webhooks, each with a minimum severity and quiet hours.

    notify:
      - ntfy: https://ntfy.sh/my-secret-topic   # or your own ntfy server
        token: tk_...                           # optional (ntfy access token)
        min: warning                            # info, warning or critical
        quiet: "22:00-07:00"                    # only critical then
      - webhook: https://example.org/hook       # POST, JSON body (title, body, severity, ...)

Messages carry device names and what is wrong; nothing secret. A channel that fails is logged
and retried with the next message, never blocking the hub.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from typing import Any

import httpx

from .alarms import Message, Severity
from .config import NotifyChannel

log = logging.getLogger(__name__)

PRIORITY = {Severity.INFO: "low", Severity.WARNING: "default", Severity.CRITICAL: "urgent"}


def in_quiet_hours(quiet: str | None, now: datetime) -> bool:
    if not quiet:
        return False
    start_text, _, end_text = quiet.partition("-")
    start, end = time.fromisoformat(start_text.strip()), time.fromisoformat(end_text.strip())
    t = now.time()
    return start <= t < end if start <= end else t >= start or t < end  # e.g. 22:00-07:00


def wants(channel: NotifyChannel, message: Message, now: datetime) -> bool:
    if message.severity.rank < Severity(channel.min).rank:
        return False
    return message.severity is Severity.CRITICAL or not in_quiet_hours(channel.quiet, now)


async def send(channels: list[NotifyChannel], messages: list[Message], now: datetime) -> None:
    """Send each message to each channel that wants it; failures are logged, not raised."""
    if not channels or not messages:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        jobs = [
            _send_one(client, channel, message)
            for message in messages
            for channel in channels
            if wants(channel, message, now)
        ]
        await asyncio.gather(*jobs)


async def _send_one(client: httpx.AsyncClient, channel: NotifyChannel, message: Message) -> None:
    title = ("✓ " if message.resolved else "") + message.title
    try:
        if channel.ntfy:
            headers: dict[str, Any] = {
                "Title": title.encode("utf-8"),
                "Priority": "default" if message.resolved else PRIORITY[message.severity],
                "Tags": "white_check_mark" if message.resolved else message.severity.value,
            }
            if channel.token:
                headers["Authorization"] = f"Bearer {channel.token}"
            r = await client.post(channel.ntfy, content=(message.body or title).encode(), headers=headers)
        else:
            assert channel.webhook
            r = await client.post(
                channel.webhook,
                json={
                    "title": title,
                    "body": message.body,
                    "severity": message.severity.value,
                    "device": message.device,
                    "resolved": message.resolved,
                },
            )
        r.raise_for_status()
    except (httpx.HTTPError, AssertionError) as e:
        log.warning("notification to %s failed: %s", channel.ntfy or channel.webhook, e)
