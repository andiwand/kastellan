import asyncio
import concurrent.futures
import json
import threading
from pathlib import Path

import asyncssh
import pytest
from conftest import password_hash
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kastellan.api import create_app
from kastellan.auth import hash_password, password_tag
from kastellan.settings import Settings
from kastellan.ssh import Keys

H = {"X-Kastellan": "1"}
HASH = password_hash()


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

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return password == "the device's password"

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
                "port": port,
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

        # Another key pinned for this host (it was reinstalled, or someone is in between): no
        # shell, and the page is told it changed (not asked to trust it like a new one).
        other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
        (tmp_path / "data" / "ssh" / "known_hosts").write_text(f"[127.0.0.1]:{port} {other}\n")
        with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c)) as ws:
            changed = json.loads(ws.receive_text())
        assert changed["type"] == "changed-host" and changed["fingerprint"] == state["fingerprint"]
        assert c.get("/api/devices/box/ssh").json()["state"] == "changed"


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
    from kastellan import terminals

    monkeypatch.setattr(terminals, "RECHECK_EVERY", 0.2)
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


def test_logging_in_without_kastellans_key(tmp_path: Path, ssh_server, admin_client) -> None:
    """A device without ssh: (only an SSH check): the page logs in with a typed password, used
    for that connection only, or stores it in the People ring and uses it from there. The host
    key is trusted first, by port (the device has no ssh: yet)."""
    port, host_key = ssh_server
    config = f"devices:\n  pi:\n    host: 127.0.0.1\n    checks:\n      - tcp: {port}\n        name: ssh\n"
    c = admin_client(config, data=tmp_path / "data", base_url="http://hub.lan")
    card = c.get("/api/devices/pi/access").json()  # the SSH access card: the key to trust
    assert card["port"] == port and card["host_key"] == {
        "state": "unknown",
        "fingerprint": host_key.get_fingerprint(),
    }
    trust = {"fingerprint": host_key.get_fingerprint(), "port": card["port"]}
    assert c.post("/api/devices/pi/ssh/trust", json=trust, headers=H).status_code == 200
    assert c.get("/api/devices/pi/access").json()["host_key"]["state"] == "trusted"
    known = (tmp_path / "data" / "ssh" / "known_hosts").read_text()
    assert known.startswith(f"[127.0.0.1]:{port} ")  # trusted by port, before the device has ssh:
    assert c.get("/api/devices").json()[0]["ssh_port"] == port

    def opened(login: dict, then: list[dict] | None = None) -> bytes:
        with c.websocket_connect("/api/devices/pi/terminal", headers=ws_headers(c)) as ws:
            asked = json.loads(ws.receive_text())
            assert asked["type"] == "login-needed" and asked["port"] == port
            for wrong in then or []:
                ws.send_text(json.dumps(wrong))
                assert json.loads(ws.receive_text())["type"] == "login-failed"
            ws.send_text(json.dumps({"type": "login", **login}))
            frame = ws.receive()
            while frame.get("text") and json.loads(frame["text"])["type"] == "note":
                frame = ws.receive()
            return frame.get("bytes") or b""

    wrong = {"type": "login", "user": "pi", "password": "nope"}
    assert b"welcome pi" in opened({"user": "pi", "password": "the device's password"}, [wrong])

    # Stored in the People ring (open after making the key rings), then used from there.
    assert c.post("/api/vault/create", json={"password": "pw for tests"}, headers=H).status_code == 200
    assert b"welcome pi" in opened({"user": "pi", "password": "the device's password", "store": True})
    stored = [e for e in c.get("/api/vault").json()["entries"] if e["ring"] == "people"]
    assert [e["id"] for e in stored] == ["ssh-pi-pi"]
    assert b"welcome pi" in opened({"entry": "ssh-pi-pi"})

    # The People ring locked: the stored login isn't offered, and asking for it is refused.
    c.post("/api/vault/lock", json={"ring": "people"}, headers=H)
    with c.websocket_connect("/api/devices/pi/terminal", headers=ws_headers(c)) as ws:
        asked = json.loads(ws.receive_text())
        assert asked["ring_open"] is False and asked["stored"] == []
        ws.send_text(json.dumps({"type": "login", "entry": "ssh-pi-pi"}))
        assert json.loads(ws.receive_text())["type"] == "login-failed"

    log = (tmp_path / "data" / "audit.log").read_text()
    assert '"way": "password"' in log and '"way": "stored"' in log
    assert "the device's password" not in log and "nope" not in log


def test_a_stored_login_stays_with_its_device(tmp_path: Path, ssh_server, admin_client) -> None:
    """A login stored for one device is never offered to, or sent to, another (a host someone
    else controls would get the password); an admin without their own slot in the People ring
    doesn't use its logins. And the host key comes first: no password for an untrusted host."""
    port, host_key = ssh_server
    checks = f"    checks:\n      - tcp: {port}\n        name: ssh\n"
    config = f"devices:\n  pi:\n    host: 127.0.0.1\n{checks}  box:\n    host: 127.0.0.1\n{checks}"
    c = admin_client(config, data=tmp_path / "data", base_url="http://hub.lan")
    with c.websocket_connect("/api/devices/pi/terminal", headers=ws_headers(c)) as ws:
        assert json.loads(ws.receive_text())["type"] == "unknown-host"  # before any password
    trust = {"fingerprint": host_key.get_fingerprint(), "port": port}
    assert c.post("/api/devices/pi/ssh/trust", json=trust, headers=H).status_code == 200
    assert c.post("/api/vault/create", json={"password": "pw for tests"}, headers=H).status_code == 200
    with c.websocket_connect("/api/devices/pi/terminal", headers=ws_headers(c)) as ws:
        assert json.loads(ws.receive_text())["type"] == "login-needed"
        ws.send_text(
            json.dumps({"type": "login", "user": "pi", "password": "the device's password", "store": True})
        )
        frame = ws.receive()
        while frame.get("text") and json.loads(frame["text"])["type"] == "note":
            frame = ws.receive()
        assert b"welcome pi" in (frame.get("bytes") or b"")

    with c.websocket_connect("/api/devices/box/terminal", headers=ws_headers(c)) as ws:
        asked = json.loads(ws.receive_text())
        assert asked["type"] == "login-needed" and asked["stored"] == []  # pi's, not box's
        ws.send_text(json.dumps({"type": "login", "entry": "ssh-pi-pi"}))
        assert json.loads(ws.receive_text())["type"] == "login-failed"

    # Only an admin's own slot made with the password they have now counts.
    vault = c.app.state.hub.vault
    assert vault.has_slot("people", "ada", password_tag(HASH))
    assert not vault.has_slot("people", "ada", "another password's tag")
    assert not vault.has_slot("people", "bob", password_tag(HASH))  # never signed in
