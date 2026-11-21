"""Sending alarms: ntfy, webhooks, Gotify, Telegram, e-mail and web push, each with a minimum
severity and quiet hours.

    notify:
      - ntfy: https://ntfy.sh/my-secret-topic   # or your own ntfy server
        token: tk_...                           # optional (ntfy access token)
        min: warning                            # info, warning or critical
        quiet: "22:00-07:00"                    # only critical then
      - webhook: https://example.org/hook       # POST, JSON body (title, body, severity, ...)
      - gotify: https://gotify.example.org      # with an application token
        token: A1b2...
      - telegram: 123456789                     # the chat id; the bot's token:
        token: "123456:ABC-..."
      - email: me@example.org
        smtp: smtps://kastellan%40example.org:password@mail.example.org:465   # or smtp://...:587
        sender: kastellan@example.org           # From (default: the smtp user)
      - push: ada                               # ada's browsers that allowed it (push.py); * all

Messages carry device names and what is wrong; nothing secret. Quiet hours are in the hub's local
time (TZ). `send` returns what could not be delivered; the hub retries it with growing pauses
(the outage that matters most, the internet line, is when ntfy.sh can't be reached).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, time
from email.message import EmailMessage
from urllib.parse import unquote, urlsplit

import httpx

from .alarms import Message, Severity
from .config import NotifyChannel
from .push import Push

log = logging.getLogger(__name__)

JOB_LIMIT = 45  # seconds for one message on one channel (e-mail may take 30)

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
    channels: list[NotifyChannel],
    messages: list[Message],
    now: datetime,
    transport: httpx.AsyncBaseTransport | None = None,  # tests
    push: Push | None = None,  # the hub's browsers, for push: channels
    secrets: Callable[[str], str] | None = None,  # `secret:<name>` in a channel: its value
) -> list[tuple[NotifyChannel, Message]]:
    """Send each message to each channel that wants it; returns what failed (to retry)."""
    if not channels or not messages:
        return []
    jobs = [(c, m) for m in messages for c in channels if wants(c, m, now)]
    if not jobs:
        return []

    async def bounded(client: httpx.AsyncClient, c: NotifyChannel, m: Message) -> bool:
        # All at once, each within a limit: one slow channel never holds the others back.
        try:
            c = resolved(c, secrets)
        except Exception as e:  # the secret isn't set, or its key ring is locked: retried later
            log.warning("notify: %s: %s", c.kind, type(e).__name__)
            return False
        try:
            return await asyncio.wait_for(_send_one(client, c, m, push), JOB_LIMIT)
        except TimeoutError:
            log.warning("notify: %s took too long", _where(c))
            return False
        except Exception as e:
            log.warning("notify: %s: %s", c.kind, type(e).__name__)
            return False

    async with httpx.AsyncClient(timeout=10, transport=transport) as client:
        results = await asyncio.gather(*(bounded(client, c, m) for c, m in jobs))
    return [job for job, ok in zip(jobs, results, strict=True) if not ok]


def resolved(channel: NotifyChannel, secrets: Callable[[str], str] | None) -> NotifyChannel:
    """The channel with its `secret:<name>` references replaced by the secrets' values (a copy:
    the values never go back into the config)."""
    refs = channel.secret_refs()
    if not refs:
        return channel
    if secrets is None:
        raise KeyError("no secrets to resolve references with")
    values = channel.model_dump() | {field: secrets(name) for field, name in refs.items()}
    result = NotifyChannel.model_validate(values)
    for field in ("ntfy", "webhook", "gotify", "smtp"):
        if raw := getattr(result, field):
            url = urlsplit(raw)
            if not url.hostname or url.port == 0:
                raise ValueError("a notification URL needs a valid host and port")
    if result.secret_refs():
        raise ValueError("a notification secret cannot contain another secret reference")
    return result


def _where(channel: NotifyChannel) -> str:
    """A channel for the log, without its secret part (an ntfy topic, a token, a password)."""
    kind = channel.kind
    if kind == "telegram":
        return "telegram"
    if kind == "email":
        return f"email via {urlsplit(channel.smtp or '').hostname}"
    if kind == "push":
        return f"push to {channel.push}"
    raw = channel.ntfy or channel.webhook or channel.gotify or ""
    if raw.startswith("secret:"):
        return f"{kind} ({raw})"
    url = httpx.URL(raw)
    return f"{kind} at {url.host}"


def _header(text: str) -> str:
    """A header value that survives proxies: ASCII as is, else RFC 2047 (ntfy decodes it)."""
    if text.isascii():
        return text
    return "=?UTF-8?B?" + base64.b64encode(text.encode()).decode() + "?="


GOTIFY_PRIORITY = {Severity.INFO: 2, Severity.WARNING: 5, Severity.CRITICAL: 8}
MARK = {Severity.INFO: "\N{INFORMATION SOURCE}\ufe0f", Severity.WARNING: "⚠️", Severity.CRITICAL: "🔴"}


async def _send_one(
    client: httpx.AsyncClient, channel: NotifyChannel, message: Message, push: Push | None = None
) -> bool:
    title = message.title  # alarms.py writes it whole ("... is back up", "Resolved: ...")
    try:
        match channel.kind:
            case "ntfy":
                headers = {
                    "Title": _header(title),
                    "Priority": "default" if message.resolved else PRIORITY[message.severity],
                    "Tags": "white_check_mark" if message.resolved else message.severity.value,
                }
                if channel.token:
                    headers["Authorization"] = f"Bearer {channel.token}"
                r = await client.post(
                    channel.ntfy or "", content=(message.body or title).encode(), headers=headers
                )
            case "gotify":
                r = await client.post(
                    (channel.gotify or "").rstrip("/") + "/message",
                    headers={"X-Gotify-Key": channel.token or ""},
                    json={
                        "title": title,
                        "message": message.body or title,
                        "priority": 2 if message.resolved else GOTIFY_PRIORITY[message.severity],
                    },
                )
            case "telegram":
                mark = "✅" if message.resolved else MARK[message.severity]
                r = await client.post(
                    f"https://api.telegram.org/bot{channel.token}/sendMessage",
                    json={
                        "chat_id": channel.telegram,
                        "text": f"{mark} {title}" + (f"\n{message.body}" if message.body else ""),
                        "disable_web_page_preview": True,
                    },
                )
            case "email":
                await asyncio.wait_for(asyncio.to_thread(_mail, channel, message), 30)
                return True
            case "push":
                if push is None:
                    return True  # nothing to push to (the demo, a test send without the hub)
                mark = "✅" if message.resolved else MARK[message.severity]
                payload = {
                    "title": f"{mark} {title}",
                    "body": message.body,
                    "url": f"/devices/{message.device}" if message.device else "/",
                    "tag": message.device or title,
                }
                # Push tries each browser again by itself (push.py): one that is away must not
                # make the others get it twice.
                await push.send(client, channel.push or "", payload)
                return True
            case _:
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
        # Only the kind of error: a Telegram URL carries the bot's token, an SMTP error the login.
        log.warning("notification to %s failed: %s", _where(channel), type(e).__name__)
        return False


def _mail(channel: NotifyChannel, message: Message) -> None:
    """One e-mail over SMTP: smtps:// is TLS from the start, smtp:// must offer STARTTLS (never
    sent in clear: it carries the login)."""
    url = urlsplit(channel.smtp or "")
    user, password = unquote(url.username or ""), unquote(url.password or "")
    sender = channel.sender or (user if "@" in user else f"kastellan@{url.hostname}")
    mail = EmailMessage()
    mail["Subject"] = (
        "Resolved: " if message.resolved and not message.title.startswith("Resolved") else ""
    ) + (message.title)
    mail["From"] = sender
    mail["To"] = channel.email or ""
    mail.set_content((message.body or message.title) + "\n\n-- Kastellan\n")
    context = ssl.create_default_context()
    if url.scheme == "smtps":
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            url.hostname or "", url.port or 465, timeout=20, context=context
        )
    else:
        smtp = smtplib.SMTP(url.hostname or "", url.port or 587, timeout=20)
    with smtp:
        if url.scheme == "smtp":
            smtp.starttls(context=context)
        if user:
            smtp.login(user, password)
        smtp.send_message(mail)
