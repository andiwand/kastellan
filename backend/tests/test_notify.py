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


async def test_invalid_secret_does_not_stop_other_channels_or_leak(caplog) -> None:
    bad = NotifyChannel(ntfy="secret:topic")
    good = NotifyChannel(ntfy="https://ntfy.test/good")
    seen = []

    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(200)

    for secret in ("private invalid URL", "secret:another", "https://[private"):
        failed = await notify.send(
            [bad, good], [DOWN], NOW, transport=httpx.MockTransport(handle), secrets=lambda _, s=secret: s
        )
        assert failed == [(bad, DOWN)]
        assert secret not in caplog.text
    assert seen == ["https://ntfy.test/good"] * 3


def test_internal_keys_cannot_be_used_as_notification_credentials() -> None:
    from kastellan.config import Config

    for key in ("hub-backup-key", "device-backups", "group-fingerprints", "syslog-ca-key"):
        with pytest.raises(ValueError, match="encryption or fingerprint key"):
            Config.model_validate({"notify": [{"telegram": 1, "token": f"secret:{key}"}]})


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


async def test_channels_may_name_secrets_instead_of_holding_them() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("authorization")))
        return httpx.Response(200)

    channel = NotifyChannel.model_validate({"ntfy": "secret:ntfy-url", "token": "secret:ntfy-token"})
    assert channel.secret_refs() == {"ntfy": "ntfy-url", "token": "ntfy-token"}
    values = {"ntfy-url": "https://ntfy.example/topic", "ntfy-token": "tk_1"}
    message = Message(Severity.CRITICAL, "down", "", None)
    failed = await notify.send(
        [channel], [message], datetime.now(UTC), httpx.MockTransport(handler), secrets=values.__getitem__
    )
    assert not failed and seen == [("https://ntfy.example/topic", "Bearer tk_1")]
    assert channel.token == "secret:ntfy-token"  # the value never goes back into the config
    failed = await notify.send([channel], [message], datetime.now(UTC), httpx.MockTransport(handler))
    assert failed  # no secrets to resolve with: retried later, not sent to "secret:..."
    tg = NotifyChannel.model_validate({"telegram": 123, "token": "secret:bot"})
    assert tg.secret_refs() == {"token": "bot"}


async def test_what_each_secret_is_used_for(tmp_path) -> None:
    from kastellan import credentials
    from kastellan.hub import Hub
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  ap:\n    host: 192.168.1.2\n    openwrt: {}\n"
        "notify:\n  - ntfy: https://ntfy.example/x\n    token: secret:ntfy-token\n"
    )
    hub = Hub(Settings(config=config, data=tmp_path))
    hub.reload()
    hub.secrets.set("openwrt-ap", "pw")
    rows = {r["name"]: r for r in credentials.view(hub)}
    assert rows["openwrt-ap"]["set"] and rows["openwrt-ap"]["used_by"] == ["ap: Kastellan's OpenWrt login"]
    assert rows["ntfy-token"]["missing"] and rows["ntfy-token"]["used_by"] == ["notify #1 (ntfy): token"]
