"""Sending alarms: ntfy and webhooks, each with a minimum severity and quiet hours.

    notify:
      - ntfy: https://ntfy.sh/my-secret-topic   # or your own ntfy server
        token: tk_...                           # optional (ntfy access token)
        min: warning                            # info, warning or critical
        quiet: "22:00-07:00"                    # only critical then
      - webhook: https://example.org/hook       # POST, JSON body (title, body, severity, ...)

Messages carry device names and what is wrong; nothing secret. Quiet hours are in the hub's local
time (TZ). `send` returns what could not be delivered; the hub retries it with growing pauses
(the outage that matters most, the internet line, is when ntfy.sh can't be reached).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, time

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
    t = now.astimezone().time()  # local time: "22:00-07:00" means the evening where the hub is
    return start <= t < end if start <= end else t >= start or t < end  # e.g. 22:00-07:00


def wants(channel: NotifyChannel, message: Message, now: datetime) -> bool:
    if message.severity.rank < Severity(channel.min).rank:
        return False
    return message.severity is Severity.CRITICAL or not in_quiet_hours(channel.quiet, now)


async def send(
    channels: list[NotifyChannel], messages: list[Message], now: datetime
) -> list[tuple[NotifyChannel, Message]]:
    """Send each message to each channel that wants it; returns what failed (to retry)."""
    if not channels or not messages:
        return []
    jobs = [(c, m) for m in messages for c in channels if wants(c, m, now)]
    if not jobs:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(*(_send_one(client, c, m) for c, m in jobs))
    return [job for job, ok in zip(jobs, results, strict=True) if not ok]


def _where(channel: NotifyChannel) -> str:
    """A channel for the log, without its secret part (an ntfy topic is the secret)."""
    url = httpx.URL(channel.ntfy or channel.webhook or "")
    return f"{'ntfy' if channel.ntfy else 'webhook'} at {url.host}"


def _header(text: str) -> str:
    """A header value that survives proxies: ASCII as is, else RFC 2047 (ntfy decodes it)."""
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


async def _send_one(client: httpx.AsyncClient, channel: NotifyChannel, message: Message) -> bool:
    title = message.title  # alarms.py writes it whole ("... is back up", "Resolved: ...")
    try:
        if channel.ntfy:
            headers = {
                "Title": _header(title),
                "Priority": "default" if message.resolved else PRIORITY[message.severity],
                "Tags": "white_check_mark" if message.resolved else message.severity.value,
            }
            if channel.token:
                headers["Authorization"] = f"Bearer {channel.token}"
            r = await client.post(channel.ntfy, content=(message.body or title).encode(), headers=headers)
        else:
            r = await client.post(
                channel.webhook or "",
                json={
                    "title": title,
                    "body": message.body,
                    "severity": message.severity.value,
                    "device": message.device,
                    "resolved": message.resolved,
                },
            )
        r.raise_for_status()
        return True
    except Exception as e:  # any failure is reported and retried, never raised into the hub
        log.warning("notification to %s failed: %s", _where(channel), type(e).__name__)
        return False
