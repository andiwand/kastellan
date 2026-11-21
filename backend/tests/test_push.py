"""Web push: the hub's key, subscriptions per person, what is sent (encrypted for the browser)."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import http_ece
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from test_config_edit import TEXT, H, signin

from kastellan import notify
from kastellan.alarms import Message, Severity
from kastellan.api import create_app
from kastellan.config import NotifyChannel
from kastellan.push import Push, _b64, _unb64, valid_endpoint
from kastellan.settings import Settings

ENDPOINT = "https://push.example.org/send/abc"


def browser() -> tuple[ec.EllipticCurvePrivateKey, str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return key, _b64(public), _b64(os.urandom(16))


def test_the_key_is_made_once_and_private(tmp_path: Path) -> None:
    first = Push(tmp_path).public_key
    assert len(_unb64(first)) == 65
    assert stat.S_IMODE((tmp_path / "push" / "vapid.pem").stat().st_mode) == 0o600
    assert Push(tmp_path).public_key == first


def test_only_push_services_by_name() -> None:
    assert valid_endpoint("https://fcm.googleapis.com/fcm/send/x")
    for bad in ["http://push.example.org/x", "https://192.168.1.5/x", "https://[::1]/x", "https://nas.lan/x"]:
        assert not valid_endpoint(bad), bad


async def test_sent_encrypted_and_gone_browsers_dropped(tmp_path: Path) -> None:
    push = Push(tmp_path)
    key, p256dh, auth = browser()
    push.add("ada", ENDPOINT, p256dh, auth, "Firefox")
    push.add("ada", "https://push.example.org/send/old", *browser()[1:], "old phone")
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201 if request.url.path.endswith("abc") else 410)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        assert await push.send(client, "ada", {"title": "NAS is down", "url": "/devices/nas"})
    r = next(r for r in seen if r.url.path.endswith("abc"))
    assert r.headers["content-encoding"] == "aes128gcm" and r.headers["authorization"].startswith("vapid t=")
    payload = json.loads(
        http_ece.decrypt(r.content, private_key=key, auth_secret=_unb64(auth), version="aes128gcm")
    )
    assert payload == {"title": "NAS is down", "url": "/devices/nas"}
    assert [s["label"] for s in push.of("ada")] == ["Firefox"]  # the 410 one is gone
    assert [s["label"] for s in Push(tmp_path).of("ada")] == ["Firefox"]  # and saved so


async def test_a_failed_push_is_tried_again(tmp_path: Path) -> None:
    push = Push(tmp_path)
    push.add("ada", ENDPOINT, *browser()[1:], "Firefox")
    transport = httpx.MockTransport(lambda r: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        assert not await push.send(client, "ada", {"title": "x"})
    assert push.of("ada")  # kept: the push service was only away


async def test_a_push_channel_has_quiet_hours(tmp_path: Path) -> None:
    push = Push(tmp_path)
    push.add("ada", ENDPOINT, *browser()[1:], "Firefox")
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(201))
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    local = now.astimezone().strftime("%H:%M")
    quiet = f"{local}-{(now.astimezone().hour + 1) % 24:02d}:00"
    c = NotifyChannel.model_validate({"push": "ada", "min": "info", "quiet": quiet})
    warning = Message(Severity.WARNING, "ssh on NAS is down", "", "nas")
    critical = Message(Severity.CRITICAL, "NAS is down", "no reply", "nas")
    failed = await notify.send([c], [warning, critical], now, transport=transport, push=push)
    assert not failed and len(seen) == 1  # only the critical one, in quiet hours


def test_channel_names_a_user() -> None:
    assert NotifyChannel.model_validate({"push": "*"}).kind == "push"
    with pytest.raises(ValueError):
        NotifyChannel.model_validate({"push": "a b"})


def test_each_person_their_own_browsers(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text(TEXT + "notify:\n  - push: obi\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.get("/api/push").status_code == 401  # signed out
        signin(c, "obi", "observer password")  # observers too: they read alarms
        sub = {"endpoint": ENDPOINT, "keys": dict(zip(["p256dh", "auth"], browser()[1:], strict=True))}
        assert c.post("/api/push/subscribe", json=sub | {"label": "Safari"}, headers=H).status_code == 200
        bad = sub | {"endpoint": "https://192.168.1.1/x"}
        assert c.post("/api/push/subscribe", json=bad, headers=H).status_code == 400
        mine = c.get("/api/push").json()
        assert mine["channel"] and [s["label"] for s in mine["subscriptions"]] == ["Safari"]
        assert c.post("/api/notify/test", headers=H).status_code == 403  # still nothing else
        c.post("/api/auth/logout", headers=H)
        signin(c)
        assert c.get("/api/push").json()["subscriptions"] == []  # ada sees only hers
        assert c.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=H).json() == {
            "removed": False
        }
        c.post("/api/auth/logout", headers=H)
        signin(c, "obi", "observer password")
        assert c.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}, headers=H).json() == {
            "removed": True
        }
    audit = (tmp_path / "audit.log").read_text()
    assert "push.subscribe" in audit and "push.unsubscribe" in audit
