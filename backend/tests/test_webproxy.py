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
                ("set-cookie", "device_login=private; Path=/; HttpOnly"),
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


def enter(c: TestClient, id: str = "nas_box") -> None:
    """Open the device's page from the hub: the ticket, traded on its name for its cookie."""
    r = c.get(f"{HUB}/api/devices/{id}/web", follow_redirects=False)
    assert r.status_code == 303
    where = r.headers["location"]
    assert where.startswith(f"http://{webproxy.label(id)}.kastellan.test{webproxy.ENTER}?ticket=")
    r = c.get(where, follow_redirects=False)
    assert r.status_code == 200 and 'http-equiv="refresh"' in r.text


def test_forwards_to_the_device_for_admins(client: TestClient, device) -> None:
    url = "http://nas-box.kastellan.test/status?x=1"
    assert client.get(url).status_code == 401  # no access to this name
    signin(client)
    # The hub's session stays on the hub's name: device names don't get it.
    assert all(c.domain in ("kastellan.test", "kastellan.test.local") for c in client.cookies.jar)
    assert client.get(url).status_code == 401  # signed in at the hub, but not opened from it
    enter(client)
    web = [c for c in client.cookies.jar if c.name == webproxy.WEB_COOKIE]
    assert [c.domain for c in web] == ["nas-box.kastellan.test"]  # host-only, on its own name
    client.cookies.set("sid", "dev", domain="nas-box.kastellan.test")  # the device's own
    r = client.get(url, headers={"X-Kastellan": "1"})
    assert r.status_code == 200 and r.text == "device page /status"
    sent = device[-1]
    assert str(sent.url) == "http://10.9.9.9:8080/status?x=1"
    assert sent.headers["cookie"] == "sid=dev" and "x-kastellan" not in sent.headers
    # The device's cookies stay on its own name; it can't set Kastellan's.
    cookies = r.headers.get_list("set-cookie")
    assert cookies == ["sid=abc; Path=/; HttpOnly", "device_login=private; Path=/; HttpOnly"]
    assert (
        r.headers["origin-agent-cluster"] == "?1"
        and r.headers["cross-origin-resource-policy"] == "same-origin"
    )
    assert "x-frame-options" not in r.headers  # the hub's own headers stay on the hub
    r = client.get("http://nas-box.kastellan.test/go", follow_redirects=False)
    assert r.headers["location"] == "http://nas-box.kastellan.test/home"
    # Opening is audited when it's opened from the hub, not per request.
    log = (client.app.state.hub.settings.data / "audit.log").read_text()  # type: ignore[attr-defined]
    assert sum(json.loads(ln)["action"] == "web.open" for ln in log.splitlines()) == 1
    # Signing out at the hub ends the device name's access too.
    client.post(f"{HUB}/api/auth/logout", headers=H)
    assert client.get(url).status_code == 401


def test_device_cookies_are_not_shared_between_browsers(client: TestClient, device) -> None:
    signin(client)
    enter(client)
    url = "http://nas-box.kastellan.test/"
    assert client.get(url).status_code == 200
    # Another browser with its own access to the device, but no device login yet.
    access = next(c.value for c in client.cookies.jar if c.name == webproxy.WEB_COOKIE)
    assert client.get(url, headers={"Cookie": f"{webproxy.WEB_COOKIE}={access}"}).status_code == 200
    assert "cookie" not in device[-1].headers
    # Only the requesting browser's login reaches the device.
    assert client.get(url, headers={"Cookie": f"{webproxy.WEB_COOKIE}={access}; sid=mine"}).status_code == 200
    assert device[-1].headers["cookie"] == "sid=mine"


async def test_proxy_client_does_not_keep_device_cookies() -> None:
    async with webproxy.new_client() as client:
        response = httpx.Response(
            200, headers={"set-cookie": "sid=private; Path=/"}, request=httpx.Request("GET", "http://nas/")
        )
        client.cookies.extract_cookies(response)
        assert not client.cookies


def test_tickets(client: TestClient, device) -> None:
    signin(client)
    r = client.get(f"{HUB}/api/devices/nas_box/web", follow_redirects=False)
    where = r.headers["location"]
    other = where.replace("nas-box.", "printer.")
    assert client.get(other).status_code == 401  # another device's name: not this ticket
    assert client.get(where).status_code == 401  # ...and it was used up by that try
    r = client.get(f"{HUB}/api/devices/nas_box/web", follow_redirects=False)
    where = r.headers["location"]
    assert client.get(where).status_code == 200
    assert client.get(where).status_code == 401  # once only
    access = webproxy.Access()
    key = access.ticket("token", "nas-box")
    access._tickets[key].expires = 0  # a minute later
    assert access.enter(key, "nas-box") is None
    assert client.get(f"{HUB}/api/devices/printer/web", follow_redirects=False).status_code == 404


def test_who_and_what_is_refused(client: TestClient, device) -> None:
    signin(client, "obi", "observer password")
    r = client.get(f"{HUB}/api/devices/nas_box/web", follow_redirects=False)
    assert r.status_code == 403  # observers open no sessions
    signin(client)
    enter(client)
    for name in ("printer", "nope", "a.nas-box"):  # its cookie is for its own name only
        assert client.get(f"http://{name}.kastellan.test/").status_code == 401
    assert not device
    # The hub itself answers at the domain; a device's name never reaches the hub's API.
    assert client.get(f"{HUB}/api/devices").status_code == 200
    assert client.get("http://nas-box.kastellan.test/api/devices").text == "device page /api/devices"


def test_another_page_cant_use_the_access(client: TestClient, device) -> None:
    signin(client)
    enter(client)
    url = "http://nas-box.kastellan.test/settings"
    for headers in (
        {"Sec-Fetch-Site": "same-site"},  # another device's page
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://printer.kastellan.test"},
        {"Origin": "null"},
    ):
        assert client.post(url, headers=headers).status_code == 403, headers
    assert not device
    ok = {"Sec-Fetch-Site": "same-origin", "Origin": "http://nas-box.kastellan.test"}
    assert client.post(url, headers=ok, content=b"x=1").status_code == 200
    assert device[-1].headers["origin"] == "http://10.9.9.9:8080"  # its own address, for its checks


def test_rewrites_only_its_own_address() -> None:
    src, dst = "http://192.168.1.1", "https://router.kastellan.test"
    assert webproxy.rebase("http://192.168.1.1/x", src, dst) == "https://router.kastellan.test/x"
    assert webproxy.rebase("http://192.168.1.1:80/x", src, dst) == "https://router.kastellan.test/x"
    assert webproxy.rebase("http://192.168.1.10/x", src, dst) == "http://192.168.1.10/x"
    assert webproxy.rebase("http://192.168.1.1:8443/x", src, dst) == "http://192.168.1.1:8443/x"
    assert webproxy.rebase("/relative", src, dst) == "/relative"


def test_a_closed_tab_ends_the_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An endless stream (a camera): the browser leaving closes the device's response too."""
    closed = asyncio.Event()

    async def endless():
        try:
            while True:
                yield b"frame"
                await asyncio.sleep(0.01)
        finally:
            closed.set()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    monkeypatch.setattr(
        webproxy, "new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    write_config(tmp_path / "config.yaml")
    hub_app = create_app(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    with TestClient(hub_app, base_url=HUB) as c:
        signin(c)
        enter(c)
        cookie = "; ".join(f"{k.name}={k.value}" for k in c.cookies.jar)

    async def run() -> None:
        messages = [{"type": "http.request", "body": b""}]
        got: list[dict] = []

        async def receive():
            if messages:
                return messages.pop()
            await asyncio.sleep(0.1)  # a moment of streaming, then the tab closes
            return {"type": "http.disconnect"}

        async def send(message):
            got.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/cam",
            "raw_path": b"/cam",
            "query_string": b"",
            "scheme": "http",
            "headers": [(b"host", b"nas-box.kastellan.test"), (b"cookie", cookie.encode())],
            "client": ("127.0.0.1", 1),
        }
        await asyncio.wait_for(hub_app(scope, receive, send), 5)
        assert got[0]["status"] == 200 and len(got) > 2
        await asyncio.wait_for(closed.wait(), 1)

    with TestClient(hub_app, base_url=HUB):  # the hub running (its lifespan), for the access
        asyncio.run(run())


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
        assert c.get(f"{HUB}/api/devices/nas_box/web", follow_redirects=False).status_code == 403
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
        enter(c)
        own = {"Origin": "http://nas-box.kastellan.test"}
        with c.websocket_connect("ws://nas-box.kastellan.test/socket", headers=own) as ws:
            ws.send_text("hi")
            assert ws.receive_text() == "echo hi"
        for origin in ({"Origin": "http://printer.kastellan.test"}, {}):  # another page's, or none
            with (
                pytest.raises(WebSocketDisconnect),
                c.websocket_connect("ws://nas-box.kastellan.test/socket", headers=origin),
            ):
                pass
    cookie = headers[0].get("cookie", "")
    assert "kastellan_session" not in cookie and webproxy.WEB_COOKIE not in cookie
    loop.call_soon_threadsafe(stop.set)
    thread.join(5)
    loop.close()


def test_open_streams_end_with_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Streams and WebSockets re-check their access: signing out ends them within a while."""
    monkeypatch.setattr(webproxy, "RECHECK_EVERY", 0.01)
    signed_in = [True]

    async def run() -> None:
        watch = asyncio.ensure_future(webproxy._watch(lambda: signed_in[0]))
        await asyncio.sleep(0.05)
        assert not watch.done()
        signed_in[0] = False
        await asyncio.wait_for(watch, 1)

    asyncio.run(run())


def test_a_device_that_never_answers_doesnt_hold_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung device web server: when the browser gives up, the proxy lets go too."""

    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError

    monkeypatch.setattr(
        webproxy, "new_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hang))
    )
    write_config(tmp_path / "config.yaml")
    hub_app = create_app(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    with TestClient(hub_app, base_url=HUB) as c:
        signin(c)
        enter(c)
        cookie = "; ".join(f"{k.name}={k.value}" for k in c.cookies.jar)

    async def run() -> None:
        got: list[dict] = []

        async def receive():
            await asyncio.sleep(0.1)
            return {"type": "http.disconnect"}

        async def send(message):
            got.append(message)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "scheme": "http",
            "headers": [(b"host", b"nas-box.kastellan.test"), (b"cookie", cookie.encode())],
            "client": ("127.0.0.1", 1),
        }
        await asyncio.wait_for(hub_app(scope, receive, send), 5)
        assert got and got[0]["status"] == 504

    with TestClient(hub_app, base_url=HUB):
        asyncio.run(run())


async def test_signing_in_for_the_admin_keeps_the_password_on_the_hub() -> None:
    import httpx
    import pytest

    from kastellan import weblogin

    def luci(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(403, text='<input name="luci_password">')
        form = dict(x.split("=") for x in request.content.decode().split("&"))
        if form == {"luci_username": "root", "luci_password": "pw"}:
            return httpx.Response(
                302, headers={"set-cookie": "sysauth_https=abc; path=/cgi-bin/luci/; HttpOnly"}
            )
        return httpx.Response(403, text='<input name="luci_password">')

    async with httpx.AsyncClient(transport=httpx.MockTransport(luci)) as client:
        s = await weblogin.sign_in(client, "https://ap", "root", "pw")
        assert s.how == "luci" and s.cookies == ["sysauth_https=abc; path=/cgi-bin/luci/; HttpOnly"]
        with pytest.raises(ValueError, match="refused"):
            await weblogin.sign_in(client, "https://ap", "root", "wrong")

    def basic(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/cgi-bin"):
            return httpx.Response(404)
        if request.headers.get("authorization") == "Basic YWRtaW46cHc=":
            return httpx.Response(200, text="printer")
        return httpx.Response(401, headers={"www-authenticate": 'Basic realm="printer"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(basic)) as client:
        s = await weblogin.sign_in(client, "http://printer", "admin", "pw")
        assert s.how == "basic" and s.basic == "Basic YWRtaW46cHc=" and not s.cookies

    access = webproxy.Access()
    key = access.ticket("session", "printer", cookies=["sid=1; Path=/"], basic="Basic x")
    value, cookies = access.enter(key, "printer")
    assert cookies == ["sid=1; Path=/"] and access.basic(value) == "Basic x"
    assert access.enter(key, "printer") is None  # once
    access.forget(value)
    assert access.basic(value) is None


def test_proxy_credentials_do_not_follow_an_upstream_change(client, device) -> None:
    signin(client)
    enter(client)
    assert client.get("http://nas-box.kastellan.test/").status_code == 200
    device.clear()
    client.app.state.hub.config.devices["nas_box"].web = "http://10.9.9.7"
    assert client.get("http://nas-box.kastellan.test/").status_code == 401
    assert not device


def test_ticket_is_refused_if_target_changes_before_opening(client, device) -> None:
    signin(client)
    link = client.get(f"{HUB}/api/devices/nas_box/web", follow_redirects=False).headers["location"]
    client.app.state.hub.config.devices["nas_box"].web = "http://10.9.9.7"
    assert client.get(link).status_code == 401
    assert not device


def test_web_login_requires_an_own_people_entry_and_keeps_credentials_on_target(client, device, monkeypatch):
    from kastellan import weblogin
    from kastellan.auth import password_tag

    signin(client)
    hub = client.app.state.hub
    hub.vault.create("ada", "admin password", password_tag(hub.config.users["ada"].password))
    for entry, ring, devices in [
        ("mine", "people", ["nas_box"]),
        ("other", "people", ["printer"]),
        ("internal", "kastellan", ["nas_box"]),
    ]:
        hub.vault.put(entry, "device-password", ring=ring, user="root", devices=devices)
    called = []

    async def sign_in(client, target, user, password):
        called.append((target, user, password))
        return weblogin.Session("basic", basic="Basic private-credentials")

    monkeypatch.setattr(weblogin, "sign_in", sign_in)
    url = f"{HUB}/api/devices/nas_box/web/sign-in"
    for entry, password, status in [
        ("mine", "wrong", 403),
        ("other", "admin password", 404),
        ("internal", "admin password", 404),
    ]:
        assert client.post(url, json={"entry": entry, "password": password}, headers=H).status_code == status
    assert not called
    r = client.post(url, json={"entry": "mine", "password": "admin password"}, headers=H)
    assert r.status_code == 200 and "private-credentials" not in r.text
    assert called == [("http://10.9.9.9:8080", "root", "device-password")]
    assert client.get(r.json()["url"]).status_code == 200
    assert client.get("http://nas-box.kastellan.test/").status_code == 200
    assert device[-1].headers["authorization"] == "Basic private-credentials"
    device.clear()
    hub.config.devices["nas_box"].web = "http://10.9.9.7"
    assert client.get("http://nas-box.kastellan.test/").status_code == 401
    assert not device
    log = hub.audit.path.read_text()
    assert "device-password" not in log and "private-credentials" not in log
