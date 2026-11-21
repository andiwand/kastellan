import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

from kastellan import webproxy
from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings

H = {"X-Kastellan": "1"}
ADMIN_HASH = hash_password("admin password")
OBSERVER_HASH = hash_password("observer password")
HUB = "http://kastellan.test"


def write_config(path: Path, upstream: str = "http://10.9.9.9:8080", users: bool = True) -> None:
    text = (
        "web:\n  domain: kastellan.test\n"
        "devices:\n"
        f"  nas_box:\n    host: 10.9.9.9\n    checks:\n      - http: {upstream}/login\n"
        "  printer:\n    host: 10.9.9.8\n"
    )
    if users:
        text += (
            "users:\n"
            f"  ada:\n    role: admin\n    password: {ADMIN_HASH}\n"
            f"  obi:\n    role: observer\n    password: {OBSERVER_HASH}\n"
        )
    path.write_text(text)


def signin(c: TestClient, user: str = "ada", password: str = "admin password") -> None:
    r = c.post(f"{HUB}/api/auth/login", json={"user": user, "password": password}, headers=H)
    assert r.status_code == 200


async def chunks(*parts: bytes):
    for part in parts:
        yield part


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch):
    """A fake device web interface; records what reached it."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/go":
            return httpx.Response(302, headers={"location": "http://10.9.9.9:8080/home"}, content=chunks())
        return httpx.Response(
            200,
            headers=[
                ("set-cookie", "sid=abc; Domain=kastellan.test; Path=/; Secure; HttpOnly"),
                ("set-cookie", "kastellan_session=evil; Path=/"),
                ("content-type", "text/plain"),
            ],
            content=chunks(b"device page ", request.url.path.encode()),  # streamed, as for real
        )

    monkeypatch.setattr(
        webproxy, "new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    return seen


@pytest.fixture
def client(tmp_path: Path):
    write_config(tmp_path / "config.yaml")
    with TestClient(create_app(Settings(config=tmp_path / "config.yaml", data=tmp_path)), base_url=HUB) as c:
        yield c


def test_forwards_to_the_device_for_admins(client: TestClient, device) -> None:
    url = "http://nas-box.kastellan.test/status?x=1"
    assert client.get(url).status_code == 401  # signed out
    signin(client)
    assert any(c.domain.lstrip(".") == "kastellan.test" for c in client.cookies.jar)  # reaches devices
    client.cookies.set("sid", "dev", domain="nas-box.kastellan.test")  # the device's own
    r = client.get(url, headers={"X-Kastellan": "1"})
    assert r.status_code == 200 and r.text == "device page /status"
    sent = device[-1]
    assert str(sent.url) == "http://10.9.9.9:8080/status?x=1"
    assert sent.headers["cookie"] == "sid=dev" and "x-kastellan" not in sent.headers
    # The device's cookies stay on its own name; it can't set Kastellan's.
    cookies = r.headers.get_list("set-cookie")
    assert cookies == ["sid=abc; Path=/; HttpOnly"]  # no Domain, no Secure over http
    r = client.get("http://nas-box.kastellan.test/go", follow_redirects=False)
    assert r.headers["location"] == "http://nas-box.kastellan.test/home"
    # Opening is audited once per session and device, not per request.
    log = (client.app.state.hub.settings.data / "audit.log").read_text()  # type: ignore[attr-defined]
    assert sum(json.loads(ln)["action"] == "web.open" for ln in log.splitlines()) == 1


def test_who_and_what_is_refused(client: TestClient, device) -> None:
    signin(client, "obi", "observer password")
    assert client.get("http://nas-box.kastellan.test/").status_code == 403  # observers open no sessions
    signin(client)
    assert client.get("http://printer.kastellan.test/").status_code == 404  # no web interface
    assert client.get("http://nope.kastellan.test/").status_code == 404
    assert client.get("http://a.nas-box.kastellan.test/").status_code == 404
    assert not device
    # The hub itself answers at the domain; a device's name never reaches the hub's API.
    assert client.get(f"{HUB}/api/devices").status_code == 200
    assert client.get("http://nas-box.kastellan.test/api/devices").text == "device page /api/devices"


def test_a_device_page_cant_drive_the_hub(client: TestClient, device) -> None:
    signin(client)
    # A write from a device's page: no X-Kastellan (it'd need the hub's CORS, which never comes).
    r = client.post(f"{HUB}/api/notify/test", headers={"Origin": "http://nas-box.kastellan.test"})
    assert r.status_code == 403
    # The hub's WebSockets open only from its own page.
    origin = {"Origin": "http://nas-box.kastellan.test"}
    url = f"{HUB.replace('http', 'ws')}/api/devices/nas_box/terminal"
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(url, headers=origin):
        pass


def test_not_while_signin_is_off(tmp_path: Path, device) -> None:
    write_config(tmp_path / "config.yaml", users=False)
    with TestClient(create_app(Settings(config=tmp_path / "config.yaml", data=tmp_path)), base_url=HUB) as c:
        assert c.get("http://nas-box.kastellan.test/").status_code == 403
    assert not device


def test_websockets_are_forwarded(tmp_path: Path) -> None:
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port: list[int] = []
    headers: list[dict[str, str]] = []

    async def echo(ws) -> None:
        headers.append(dict(ws.request.headers))
        async for message in ws:
            await ws.send(f"echo {message}")

    stop = asyncio.Event()

    async def main() -> None:
        async with serve(echo, "127.0.0.1", 0) as server:
            port.append(server.sockets[0].getsockname()[1])
            ready.set()
            await stop.wait()

    thread = threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True)
    thread.start()
    ready.wait(5)
    write_config(tmp_path / "config.yaml", upstream=f"http://127.0.0.1:{port[0]}")
    with TestClient(create_app(Settings(config=tmp_path / "config.yaml", data=tmp_path)), base_url=HUB) as c:
        signin(c)
        with c.websocket_connect("ws://nas-box.kastellan.test/socket") as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "echo hi"
    assert "kastellan_session" not in headers[0].get("cookie", "")
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)
    loop.close()
