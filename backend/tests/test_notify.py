"""Delivery to each kind of channel: what is sent where, and that secrets stay out of the log."""

import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from kastellan import notify
from kastellan.alarms import Message, Severity
from kastellan.config import NotifyChannel

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
DOWN = Message(Severity.CRITICAL, "NAS is down", "no reply", "nas")


def channel(**kw) -> NotifyChannel:
    return NotifyChannel.model_validate(kw)


async def deliver(c: NotifyChannel, message: Message = DOWN, status: int = 200):
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"ok": status < 400})

    failed = await notify.send([c], [message], NOW, transport=httpx.MockTransport(handle))
    return seen, failed


async def test_gotify() -> None:
    seen, failed = await deliver(channel(gotify="https://gotify.example.org/", token="Abc"))
    assert not failed and str(seen[0].url) == "https://gotify.example.org/message"
    assert seen[0].headers["X-Gotify-Key"] == "Abc"
    assert json.loads(seen[0].content) == {"title": "NAS is down", "message": "no reply", "priority": 8}


async def test_telegram_keeps_the_token_out_of_the_log(caplog: pytest.LogCaptureFixture) -> None:
    c = channel(telegram=-100123, token="123456:ABC-def")
    seen, failed = await deliver(c)
    assert not failed and seen[0].url.path == "/bot123456:ABC-def/sendMessage"
    body = json.loads(seen[0].content)
    assert body["chat_id"] == -100123 and body["text"] == "🔴 NAS is down\nno reply"
    with caplog.at_level(logging.WARNING):
        _, failed = await deliver(c, status=500)
    assert failed and "ABC-def" not in caplog.text and "telegram" in caplog.text


async def test_email(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            sent.append(("connect", host, port, context is not None))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent.append(("starttls",))

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, mail):
            sent.append(("mail", mail["From"], mail["To"], mail["Subject"], mail.get_content()))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    c = channel(email="me@example.org", smtp="smtp://bot%40example.org:p%40ss@mail.example.org:587")
    _, failed = await deliver(c)
    assert not failed
    assert sent[:3] == [
        ("connect", "mail.example.org", 587, False),
        ("starttls",),  # never the login in clear
        ("login", "bot@example.org", "p@ss"),
    ]
    assert sent[3][:4] == ("mail", "bot@example.org", "me@example.org", "NAS is down")
    sent.clear()
    c = channel(email="me@example.org", smtp="smtps://u:p@mail.example.org", sender="k@example.org")
    await deliver(c, Message(Severity.WARNING, "NAS is back up", "", "nas", resolved=True))
    assert sent[0] == ("connect", "mail.example.org", 465, True) and ("starttls",) not in sent
    assert sent[-1][1] == "k@example.org" and sent[-1][3] == "Resolved: NAS is back up"


@pytest.mark.parametrize(
    ("kw", "error"),
    [
        ({"gotify": "https://g.example.org"}, "token"),
        ({"telegram": "123", "token": "nope"}, "bot"),
        ({"telegram": "chat name", "token": "1:a"}, "chat id"),
        ({"email": "me@example.org"}, "smtp"),
        ({"webhook": "https://x.example", "smtp": "smtp://h"}, "email"),
        ({"ntfy": "https://ntfy.sh/x", "gotify": "https://g", "token": "t"}, "one of"),
    ],
)
def test_channel_config(kw: dict, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        NotifyChannel.model_validate(kw)
