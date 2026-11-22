import asyncio
import concurrent.futures
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kastellan import guac
from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings

H = {"X-Kastellan": "1"}
HASH = hash_password("pw for tests")


@pytest.fixture
def guacd():
    """A fake guacd on its own thread: does the 1.6 handshake, records it, then echoes."""
    seen: list[list[str]] = []
    loop = asyncio.new_event_loop()
    started: concurrent.futures.Future = concurrent.futures.Future()

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        reader = guac.Reader(r)
        seen.append(guac.decode(await reader.instruction()))
        w.write(guac.encode("args", "VERSION_1_5_0", "hostname", "port", "username", "password").encode())
        while (i := guac.decode(await reader.instruction()))[0] != "connect":
            seen.append(i)
        seen.append(i)
        w.write(guac.encode("ready", "$conn").encode())
        await w.drain()
        while instruction := await reader.instruction():
            w.write(instruction.encode())
            await w.drain()

    async def serve() -> None:
        started.set_result(await asyncio.start_server(handle, "127.0.0.1", 0))

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(serve(), loop)
    server = started.result(timeout=10)
    yield server.sockets[0].getsockname()[1], seen
    loop.call_soon_threadsafe(server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


def make_client(tmp_path: Path, guacd_port: int) -> TestClient:
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  pc:\n    host: 192.0.2.7\n    kind: pc\n    desktop:\n      protocol: rdp\n"
        f"users:\n  ada:\n    role: admin\n    password: {HASH}\n"
    )
    settings = Settings(config=config, data=tmp_path / "data", guacd=f"127.0.0.1:{guacd_port}")
    return TestClient(create_app(settings))


def headers(c: TestClient) -> dict[str, str]:
    return {"origin": "http://testserver", "cookie": "; ".join(f"{k}={v}" for k, v in c.cookies.items())}


def signin(c: TestClient) -> None:
    assert (
        c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=H).status_code
        == 200
    )


def ticket(c: TestClient) -> str:
    r = c.post(
        "/api/devices/pc/desktop/ticket", json={"username": "andreas", "password": "s3cret!"}, headers=H
    )
    assert r.status_code == 200
    return r.json()["ticket"]


def test_desktop_relay(tmp_path: Path, guacd) -> None:
    port, seen = guacd
    with make_client(tmp_path, port) as c:
        signin(c)
        t = ticket(c)
        url = f"/api/devices/pc/desktop?ticket={t}&width=1600&height=900&dpi=120"
        with c.websocket_connect(url, headers=headers(c), subprotocols=["guacamole"]) as ws:
            assert ws.receive_text().startswith("0.,36.")  # the tunnel's uuid
            ws.send_text(guac.encode("", "ping", "123"))  # answered by the hub, not guacd
            assert guac.decode(ws.receive_text()) == ["", "ping", "123"]
            ws.send_text(guac.encode("mouse", "10", "20", "1") + guac.encode("key", "65", "1"))
            expected = guac.encode("mouse", "10", "20", "1") + guac.encode("key", "65", "1")
            got = ""
            while len(got) < len(expected):  # one batch or several frames, both are fine
                got += ws.receive_text()
            assert got == expected

        # The password reached guacd once, and went nowhere else.
        assert seen[0] == ["select", "rdp"] and ["size", "1600", "900", "120"] in seen
        assert seen[-1] == ["connect", "VERSION_1_5_0", "192.0.2.7", "3389", "andreas", "s3cret!"]
        log_file = tmp_path / "data" / "audit.log"
        for _ in range(50):  # the hub writes desktop.close just after the page has gone
            if "desktop.close" in log_file.read_text():
                break
            time.sleep(0.05)
        log = log_file.read_text()
        assert "desktop.open" in log and "desktop.close" in log and "s3cret!" not in log

        # A ticket is good once.
        with (
            pytest.raises(WebSocketDisconnect),
            c.websocket_connect(url, headers=headers(c), subprotocols=["guacamole"]) as ws,
        ):
            ws.receive_text()


def test_desktop_refuses_without_ticket_or_sign_in(tmp_path: Path, guacd) -> None:
    port, _ = guacd
    with make_client(tmp_path, port) as c:
        assert c.post("/api/devices/pc/desktop/ticket", json={}, headers=H).status_code == 401
        signin(c)
        for url in ("/api/devices/pc/desktop", "/api/devices/pc/desktop?ticket=made-up"):
            with (
                pytest.raises(WebSocketDisconnect),
                c.websocket_connect(url, headers=headers(c), subprotocols=["guacamole"]) as ws,
            ):
                ws.receive_text()


def test_desktop_ends_with_the_session(tmp_path: Path, guacd, monkeypatch: pytest.MonkeyPatch) -> None:
    from kastellan import terminals

    monkeypatch.setattr(terminals, "RECHECK_EVERY", 0.2)
    port, _ = guacd
    with make_client(tmp_path, port) as c:
        signin(c)
        url = f"/api/devices/pc/desktop?ticket={ticket(c)}"
        with c.websocket_connect(url, headers=headers(c), subprotocols=["guacamole"]) as ws:
            ws.receive_text()
            assert c.post("/api/auth/logout?everywhere=true", headers=H).status_code == 200
            frame = ws.receive()
            assert frame.get("text") and guac.decode(frame["text"])[0] == "error"


def test_only_screen_input_reaches_guacd(tmp_path: Path, guacd) -> None:
    """Security review: the browser could send any instruction to guacd."""
    port, _ = guacd
    with make_client(tmp_path, port) as c:
        signin(c)
        url = f"/api/devices/pc/desktop?ticket={ticket(c)}"
        with c.websocket_connect(url, headers=headers(c), subprotocols=["guacamole"]) as ws:
            ws.receive_text()  # uuid
            ws.send_text(
                guac.encode("file", "1", "text/plain", "x.txt")
                + guac.encode("argv", "2", "text/plain", "hostname")
                + guac.encode("size", "99999", "99999")
                + guac.encode("mouse", "1", "2", "0")
            )
            expected = guac.encode("size", "7680", "4320") + guac.encode("mouse", "1", "2", "0")
            got = ""
            while len(got) < len(expected):  # the fake guacd echoes what reached it
                got += ws.receive_text()
            assert got == expected
