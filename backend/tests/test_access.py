import asyncio
import concurrent.futures
import subprocess
import threading
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient

from kastellan.access import hub_address, setup_script
from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings

H = {"X-Kastellan": "1"}
HASH = hash_password("pw for tests")


def test_scripts() -> None:
    own = setup_script("andreas", "ssh-ed25519 AAAA kastellan-hub", "192.168.15.50", create=False, sudo=False)
    assert 'from="192.168.15.50" ssh-ed25519 AAAA kastellan-hub' in own
    assert "useradd" not in own and "sudoers" not in own
    dedicated = setup_script("kastellan", "ssh-ed25519 AAAA kastellan-hub", None, create=True, sudo=True)
    assert "useradd --create-home" in dedicated and "/etc/sudoers.d/kastellan-kastellan" in dedicated
    for script in (own, dedicated):
        assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    with pytest.raises(ValueError):
        setup_script("bad;user", "k", None, False, False)
    assert hub_address("127.0.0.1") == "127.0.0.1"


@pytest.fixture
def device():
    """A device that accepts one login with a password and records what it is asked to run."""
    ran: list[tuple[str, str]] = []
    loop = asyncio.new_event_loop()
    started: concurrent.futures.Future = concurrent.futures.Future()
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return (username, password) == ("andreas", "hunter2")

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        ran.append((process.command or "", await process.stdin.read()))
        process.stdout.write("Kastellan can log in as kastellan.\n")
        process.exit(0)

    async def serve() -> None:
        started.set_result(
            await asyncssh.create_server(
                Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=handle
            )
        )

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(serve(), loop)
    server = started.result(timeout=10)
    yield server.sockets[0].getsockname()[1], host_key, ran
    loop.call_soon_threadsafe(server.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


def test_set_up_with_a_password_once(tmp_path: Path, device) -> None:
    port, host_key, ran = device
    config = tmp_path / "config.yaml"
    config.write_text(
        f"devices:\n  sv1:\n    host: 127.0.0.1\nusers:\n  ada:\n    role: admin\n    password: {HASH}\n"
    )
    with TestClient(create_app(Settings(config=config, data=tmp_path / "data"))) as c:
        assert (
            c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=H).status_code
            == 200
        )
        hub = c.app.state.hub
        hub.keys.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        hub.keys.known_hosts.write_text(
            f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n"
        )

        body = {
            "login": "andreas",
            "password": "wrong",
            "user": "kastellan",
            "create": True,
            "sudo": True,
            "port": port,
        }
        assert c.post("/api/devices/sv1/access/setup", json=body, headers=H).status_code == 401
        body["password"] = "hunter2"
        r = c.post("/api/devices/sv1/access/setup", json=body, headers=H)
        assert r.status_code == 200, r.text
        assert r.json()["config"] == f"ssh:\n  user: kastellan\n  port: {port}"
        [(command, stdin)] = ran
        assert command.startswith("sudo -S -p '' sh -c ") and "useradd" in command  # not root: through sudo
        assert stdin == "hunter2\n"  # the password reaches sudo, nothing else
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "access.setup" in log and "hunter2" not in log
