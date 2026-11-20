import asyncio
import concurrent.futures
import json
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
        if process.command == "sudo -n true":  # no passwordless sudo here: it asks for one
            process.exit(1)
            return
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
        # The output streams; the last line is the outcome.
        lines = r.text.splitlines()
        assert lines[0] == "Kastellan can log in as kastellan."
        done = json.loads(lines[-1].removeprefix("KASTELLAN-DONE "))
        assert done["ok"] and done["config"] == f"ssh:\n  user: kastellan\n  port: {port}"
        assert hub.config.devices["sv1"].ssh.user == "kastellan"  # in config.yaml now
        [(command, stdin)] = ran
        assert command.startswith("sudo -S -p '' sh -c ") and "useradd" in command  # not root: through sudo
        assert stdin == "hunter2\n"  # the password reaches sudo, nothing else
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "access.setup" in log and "hunter2" not in log


def test_key_written_as_the_user(tmp_path: Path) -> None:
    """Root adds the key through su as the user; a missing user stops before anything is written."""
    bin = tmp_path / "bin"
    bin.mkdir()
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    stubs = {
        "getent": f'[ "$2" = alice ] && echo "alice:x:1:1::{home}:/bin/sh"',
        "su": 'echo "$3" >> "$SU_LOG"; shift 4; exec sh -c "$@"',  # su -s SH USER -c CMD ARGS
        "id": "echo root",
    }
    for name, body in stubs.items():
        (bin / name).write_text(f"#!/bin/sh\n{body}\n")
        (bin / name).chmod(0o755)
    env = {"PATH": f"{bin}:/usr/bin:/bin", "SU_LOG": str(tmp_path / "su.log")}
    for _ in range(2):
        script = setup_script("alice", "ssh-ed25519 AAAA k", "10.0.0.5", create=False, sudo=False)
        assert subprocess.run(["sh"], input=script, text=True, env=env).returncode == 0
    keys = home / ".ssh" / "authorized_keys"
    assert keys.read_text() == 'from="10.0.0.5" ssh-ed25519 AAAA k\n'  # once
    assert (tmp_path / "su.log").read_text().split() == ["alice", "alice"]
    assert keys.stat().st_mode & 0o777 == 0o600
    bob = setup_script("bob", "ssh-ed25519 AAAA k", "10.0.0.5", create=False, sudo=False)
    r = subprocess.run(["sh"], input=bob, text=True, env=env, capture_output=True)
    assert r.returncode == 1 and "no user bob" in r.stderr
    # dropbear (OpenWrt) gets the plain line: it would refuse from=
    assert "line='ssh-ed25519 AAAA k'; else line='from=" in bob
    assert setup_script("john.doe", "k", None, False, True).count("/etc/sudoers.d/kastellan-john_doe") >= 1


def test_installing_the_collector(tmp_path: Path, device) -> None:
    port, host_key, ran = device
    config = tmp_path / "config.yaml"
    users = f"users:\n  ada:\n    role: admin\n    password: {HASH}\n"
    config.write_text("devices:\n  sv1:\n    host: 127.0.0.1 # SV1\n" + users)
    with TestClient(create_app(Settings(config=config, data=tmp_path / "data"))) as c:
        c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=H)
        hub = c.app.state.hub
        hub.keys.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        hub.keys.known_hosts.write_text(
            f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n"
        )

        setup = c.get("/api/devices/sv1/collect").json()
        assert setup["state"] == "off" and "kastellan-collect" in setup["script"]
        assert (
            hub.keys.public_key("collect") in setup["script"] and hub.keys.public_key() not in setup["script"]
        )

        body = {"login": "andreas", "password": "hunter2", "port": port}
        r = c.post("/api/devices/sv1/collect/install", json=body, headers=H)
        assert r.status_code == 200, r.text
        [(command, stdin)] = ran
        assert command.startswith("sudo -S -p '' sh -c ") and "/usr/local/lib/kastellan/collect" in command
        assert stdin == "hunter2\n"
        # Kastellan reads it from now on: collect: added, the rest of the file as it was.
        text = config.read_text()
        assert f"host: 127.0.0.1 # SV1\n    collect:\n      port: {port}\n" in text
        assert hub.config.devices["sv1"].collect is not None
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "collect.install" in log and "hunter2" not in log


def test_installing_the_actor(tmp_path: Path, device) -> None:
    port, host_key, ran = device
    config = tmp_path / "config.yaml"
    users = f"users:\n  ada:\n    role: admin\n    password: {HASH}\n"
    config.write_text("devices:\n  sv1:\n    host: 127.0.0.1\n" + users)
    with TestClient(create_app(Settings(config=config, data=tmp_path / "data"))) as c:
        c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=H)
        hub = c.app.state.hub
        hub.keys.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        hub.keys.known_hosts.write_text(
            f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n"
        )
        # Nothing to act through yet.
        assert c.post("/api/devices/sv1/act/reboot", json={}, headers=H).status_code == 409
        choice = {"abilities": ["update-container"], "containers": ["norn"]}
        preview = c.post("/api/devices/sv1/act/script", json=choice, headers=H).json()
        assert preview["configured"] is False and "act/update-container *" in preview["script"]
        assert "act/reboot" in preview["script"]  # only to remove it: "rm -f ..."
        assert (
            c.post("/api/devices/sv1/act/script", json={"abilities": ["format-disk"]}, headers=H).status_code
            == 400
        )

        r = c.post(
            "/api/devices/sv1/act/install",
            json=choice | {"login": "andreas", "password": "hunter2", "port": port},
            headers=H,
        )
        assert r.status_code == 200, r.text
        [(command, stdin)] = ran
        assert (
            command.startswith("sudo -S -p '' sh -c ") and "/usr/local/lib/kastellan/act/dispatch" in command
        )
        assert stdin == "hunter2\n"
        assert hub.config.devices["sv1"].act is not None
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "act.install" in log and "hunter2" not in log and '"update-container"' in log
