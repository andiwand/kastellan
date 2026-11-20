import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings
from kastellan.ssh import Keys

H = {"X-Kastellan": "1"}
HASH = hash_password("pw for tests")


@pytest.fixture
def ssh_server(tmp_path: Path):
    """A local SSH server that accepts the hub's key and echoes a typed line. It runs on its own
    thread and loop: the TestClient blocks the test's loop while it waits for the app."""
    keys = Keys(tmp_path / "data" / "ssh")
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    loop = asyncio.new_event_loop()
    started: concurrent.futures.Future = concurrent.futures.Future()

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

    async def shell(process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write(f"$ welcome {process.get_extra_info('username')}\r\n")
        while True:  # echo lines until "exit" (or the hub closes the session)
            line = await process.stdin.readline()
            if not line or line.strip() == "exit":
                break
            process.stdout.write(f"you typed {line.strip()}\r\n")
        process.exit(0)

    async def serve() -> None:
        server = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[host_key],
            authorized_client_keys=asyncssh.import_authorized_keys(keys.public_key()),
            process_factory=shell,
        )
        started.set_result(server)

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(serve(), loop)
    server = started.result(timeout=10)
    yield server.sockets[0].getsockname()[1], host_key
    loop.call_soon_threadsafe(server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


def make_client(tmp_path: Path, port: int, users: bool = True) -> TestClient:
    config = tmp_path / "config.yaml"
    text = f"devices:\n  box:\n    host: 127.0.0.1\n    ssh:\n      user: root\n      port: {port}\n"
    if users:
        text += f"users:\n  ada:\n    role: admin\n    password: {HASH}\n"
        text += f"  obi:\n    role: observer\n    password: {HASH}\n"
    config.write_text(text)
    return TestClient(create_app(Settings(config=config, data=tmp_path / "data")), base_url="http://hub.lan")


def signin(c: TestClient, user: str) -> None:
    assert (
        c.post("/api/auth/login", json={"user": user, "password": "pw for tests"}, headers=H).status_code
        == 200
    )


ORIGIN = {"origin": "http://hub.lan"}


def ws_headers(c: TestClient, origin: str | None = "http://testserver") -> dict[str, str]:
    """A browser sends the page's origin and the session cookie with a WebSocket (the test client
    always says Host: testserver); Starlette's
    test client sends neither by itself."""
    headers = {"cookie": "; ".join(f"{k}={v}" for k, v in c.cookies.items())}
    if origin:
        headers["origin"] = origin
    return headers


def test_terminal_end_to_end(tmp_path: Path, ssh_server) -> None:
    port, host_key = ssh_server
    with make_client(tmp_path, port) as c:
        signin(c, "ada")
        state = c.get("/api/devices/box/ssh").json()
        assert state["state"] == "unknown" and state["fingerprint"] == host_key.get_fingerprint()

        with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c)) as ws:
            assert json.loads(ws.receive_text()) == {
                "type": "unknown-host",
                "fingerprint": state["fingerprint"],
            }

        trusted = c.post("/api/devices/box/ssh/trust", json={"fingerprint": state["fingerprint"]}, headers=H)
        assert trusted.status_code == 200
        assert c.get("/api/devices/box/ssh").json()["state"] == "trusted"

        with c.websocket_connect("/api/devices/box/terminal?cols=80&rows=24", headers=ws_headers(c)) as ws:
            assert b"welcome root" in ws.receive_bytes()
            ws.send_text(json.dumps({"type": "input", "data": "secret-ish\n"}))
            out = b""
            while b"you typed" not in out:
                out += ws.receive_bytes()
            assert b"secret-ish" in out
            ws.send_text(json.dumps({"type": "input", "data": "exit\n"}))
            frame = ws.receive()
            while not frame.get("text"):  # output still on its way
                frame = ws.receive()
            assert json.loads(frame["text"])["type"] == "exit"

        log = (tmp_path / "data" / "audit.log").read_text()
        assert "terminal.open" in log and "terminal.close" in log and "ssh.trust" in log
        assert "secret-ish" not in log  # what is typed never goes to the audit log


def refused(c: TestClient, origin: str | None = "http://testserver") -> bool:
    """Whether the hub closes a terminal before any shell starts."""
    try:
        with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c, origin)) as ws:
            ws.receive_text()
    except WebSocketDisconnect:
        return True
    return False


def test_terminal_refuses(tmp_path: Path, ssh_server) -> None:
    port, _ = ssh_server
    with make_client(tmp_path, port) as c:
        assert refused(c)  # signed out: the guard closes it
        signin(c, "obi")
        assert refused(c)  # observers get no shell
        signin(c, "ada")
        assert not refused(c)  # the same browser, signed in as an admin: allowed
        assert refused(c, "http://evil.example")  # another site's page, with the user's cookie
        assert refused(c, None)  # no Origin at all

    # A fresh hub where sign-in was never on (its own data folder; see hub.signin_required).
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with make_client(fresh, port, users=False) as c:  # sign-in off: terminals stay locked
        assert refused(c)
        r = c.post("/api/devices/box/ssh/trust", json={"fingerprint": "x"}, headers=H)
        assert r.status_code == 403


def ends(ws) -> str:
    """The reason the hub gives when it ends a terminal on its own."""
    while True:
        try:
            frame = ws.receive()
        except WebSocketDisconnect:
            return "disconnected"
        if frame.get("text"):
            return json.loads(frame["text"]).get("message", "")
        if frame["type"] == "websocket.close":
            return "closed"


def test_terminal_ends_with_the_session(tmp_path: Path, ssh_server, monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review: an open terminal used to outlive sign-out, removal and password change."""
    from kastellan import terminal_api

    monkeypatch.setattr(terminal_api, "RECHECK_EVERY", 0.2)
    port, host_key = ssh_server
    with make_client(tmp_path, port) as c:
        signin(c, "ada")
        c.post("/api/devices/box/ssh/trust", json={"fingerprint": host_key.get_fingerprint()}, headers=H)

        with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c)) as ws:
            assert b"welcome" in ws.receive_bytes()
            # 1. Signing out everywhere ends it at once.
            assert c.post("/api/auth/logout?everywhere=true", headers=H).status_code == 200
            assert "signed out" in ends(ws)

        signin(c, "ada")
        with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c)) as ws:
            assert b"welcome" in ws.receive_bytes()
            # 2. So does a new password in config.yaml.
            config = tmp_path / "config.yaml"
            config.write_text(config.read_text().replace(HASH, hash_password("another password")))
            c.app.state.hub.reload()
            assert ends(ws) in ("your access changed", "your session ended")

        log = (tmp_path / "data" / "audit.log").read_text()
        assert log.count('"terminal.close"') == 2
