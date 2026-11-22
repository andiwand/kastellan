"""Keys on a device, removing one, and rotating the hub key, against an SSH server that really
runs the commands (in a temporary home) and checks logins against its authorized_keys file."""

import asyncio
import getpass
import os
from pathlib import Path

import asyncssh
import pytest

from kastellan import sshkeys
from kastellan.config import Device
from kastellan.ssh import Keys

ME = getpass.getuser()


@pytest.fixture
async def host(tmp_path: Path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # No real sudo in tests, whatever the machine allows.
    (bin_dir / "sudo").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "sudo").chmod(0o755)
    # The login's home is the temporary one (macOS has no getent, and another home anyway).
    (bin_dir / "getent").write_text(f'#!/bin/sh\necho "{ME}:x:501:20::{home}:/bin/sh"\n')
    (bin_dir / "getent").chmod(0o755)
    keys_file = home / ".ssh" / "authorized_keys"
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    class Server(asyncssh.SSHServer):
        def connection_made(self, conn):
            self.peer = conn.get_extra_info("peername")[0]

        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            try:
                allowed = asyncssh.read_authorized_keys(str(keys_file))
            except (OSError, ValueError):
                return False
            return allowed.validate(key, self.peer, self.peer) is not None

    async def run(process: asyncssh.SSHServerProcess) -> None:
        try:
            await run_one(process)
        except Exception as e:  # pragma: no cover - shows what broke in the fake server
            import traceback

            traceback.print_exc()
            process.exit(255)
            raise e

    async def run_one(process: asyncssh.SSHServerProcess) -> None:
        env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            process.command or "true",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        data = await process.stdin.read()
        out, err = await proc.communicate(data.encode() if isinstance(data, str) else data)
        process.stdout.write(out.decode(errors="replace"))
        process.stderr.write(err.decode(errors="replace"))
        process.exit(proc.returncode or 0)

    server = await asyncssh.create_server(
        Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=run
    )
    port = server.sockets[0].getsockname()[1]
    keys = Keys(tmp_path / "ssh")
    keys.hub_key()
    (tmp_path / "ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n"
    )
    other = asyncssh.generate_private_key("ssh-ed25519", comment="someone@laptop")
    keys_file.write_text(
        f'from="127.0.0.1" {keys.public_key()}\n'
        f"# a comment\n"
        f"no-pty {other.export_public_key().decode().strip()}\n"
    )
    device = Device.model_validate({"host": "127.0.0.1", "ssh": {"user": ME, "port": port}})
    yield keys, device, keys_file, other
    server.close()


async def test_list_and_remove(host) -> None:
    keys, device, keys_file, other = host
    found, everyone = await sshkeys.list_keys(keys, device)
    assert not everyone  # no root, no sudo: only the login's own
    assert [(k.user, k.comment, k.options) for k in found] == [
        (ME, "kastellan-hub", 'from="127.0.0.1"'),
        (ME, "someone@laptop", "no-pty"),
    ]
    assert found[1].fingerprint == other.get_fingerprint()

    await sshkeys.remove_lines(keys, device, ME, found[1].file, [found[1].line])
    text = keys_file.read_text()
    assert "someone@laptop" not in text and "kastellan-hub" in text and "# a comment" in text


async def test_rotating_the_hub_key(host) -> None:
    keys, device, keys_file, _ = host
    old = keys.hub_key()
    rotator = sshkeys.Rotator(keys)

    rot = await rotator.start({"box": device})
    assert rot.stage == "adding" and rot.devices["box"].added, rot.devices["box"].error
    nxt = keys.side_key("next")
    assert nxt is not None and nxt.get_fingerprint() in {
        k.fingerprint for k in (await sshkeys.list_keys(keys, device))[0]
    }
    assert 'from="127.0.0.1" ssh-ed25519' in keys_file.read_text().splitlines()[-1]

    rot = await rotator.finish({"box": device})
    assert rot.stage == "done", rot.devices
    assert keys.hub_key().get_fingerprint() == nxt.get_fingerprint()
    assert keys.side_key("old") is None and keys.side_key("next") is None
    left = {k.fingerprint for k in (await sshkeys.list_keys(keys, device))[0]}
    assert old.get_fingerprint() not in left and nxt.get_fingerprint() in left
    assert (keys.folder / "hub_ed25519.pub").read_text().split()[1] == keys.public_key().split()[1]


async def test_a_device_that_fails_holds_the_switch(host) -> None:
    keys, device, _, _ = host
    gone = Device.model_validate({"host": "127.0.0.1", "ssh": {"user": ME, "port": 1}})
    rotator = sshkeys.Rotator(keys)
    rot = await rotator.start({"box": device, "gone": gone})
    assert rot.devices["box"].added and not rot.devices["gone"].added and rot.devices["gone"].error
    with pytest.raises(ValueError, match="gone"):
        await rotator.finish({"box": device, "gone": gone})
    assert keys.side_key("old") is None  # nothing switched
    await rotator.cancel()
    assert keys.side_key("next") is None and rotator.state() is None


async def test_a_device_skipped_or_gone_holds_the_end_until_forgotten(host) -> None:
    keys, device, _, _ = host
    gone = Device.model_validate({"host": "127.0.0.1", "ssh": {"user": ME, "port": 1}})
    rotator = sshkeys.Rotator(keys)
    await rotator.start({"box": device, "gone": gone})
    rot = await rotator.finish({"box": device, "gone": gone}, anyway=True)
    # Switched without it, but it stays: the old key is still trusted there.
    assert rot.stage == "removing" and rot.devices["box"].removed
    assert not rot.devices["gone"].removed and rot.devices["gone"].error
    assert keys.side_key("old") is not None
    rot = await rotator.finish({"box": device})  # it left config.yaml: still not done
    assert rot.stage == "removing" and "forget it" in (rot.devices["gone"].error or "")
    rot = await rotator.forget("gone")
    assert rot.stage == "done" and rot.devices["gone"].forgotten and keys.side_key("old") is None
    with pytest.raises(ValueError, match="nothing to forget"):
        await rotator.forget("gone")


def test_parse_skips_what_isnt_a_key() -> None:
    out = "\n".join(
        [
            "root\t/etc/dropbear/authorized_keys\tssh-rsa AAAAB3NzaC1yc2E= broken",
            "a\t/h/.ssh/authorized_keys\troot:$6$hash:19000:0:99999:7:::",
            "a\t/h/.ssh/authorized_keys\t",
        ]
    )
    assert sshkeys.parse(out) == ([], False)


def test_only_signed_in_admins_change_keys(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    h = {"X-Kastellan": "1"}
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  box:\n    host: 127.0.0.1\n    ssh:\n      user: me\n"
        f"users:\n  obi:\n    role: observer\n    password: {hash_password('observer pw')}\n"
    )
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        c.post("/api/auth/login", json={"user": "obi", "password": "observer pw"}, headers=h)
        assert c.get("/api/ssh/keys").status_code == 403
        assert c.post("/api/ssh/rotate/start", headers=h).status_code == 403
        assert c.post("/api/ssh/rotate/forget", json={"device": "box"}, headers=h).status_code == 403
        body = {"user": "me", "file": "/x", "fingerprint": "SHA256:abcdefghijk"}
        assert c.post("/api/devices/box/keys/remove", json=body, headers=h).status_code == 403
    config.write_text("devices:\n  box:\n    host: 127.0.0.1\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path / "off"))) as c:
        assert c.get("/api/ssh/keys").status_code == 200  # sign-in off: admins may look
        r = c.post("/api/ssh/rotate/start", headers=h)
        assert r.status_code == 403 and "sign-in" in r.json()["detail"]  # but not change


def test_a_key_hidden_in_an_option_is_the_real_key() -> None:
    hub = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    evil = asyncssh.generate_private_key("ssh-ed25519")
    line = f'command="true {hub} x",no-pty {evil.export_public_key().decode().strip()} me'
    found, _ = sshkeys.parse(f"me\t/home/me/.ssh/authorized_keys\t{line}")
    assert [k.fingerprint for k in found] == [evil.get_fingerprint()]
    assert found[0].options.startswith('command="true ssh-ed25519')


def test_rows_are_lines_and_files_are_what_the_script_lists() -> None:
    key = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    trick = f"me\t/home/me/.ssh/authorized_keys\t{key} c\x0croot\t/bin/busybox\t{key} y"
    found, _ = sshkeys.parse(trick)
    assert [(k.user, k.file) for k in found] == [("me", "/home/me/.ssh/authorized_keys")]
    for row in (
        f"me\t/etc/passwd\t{key}",
        f"bad;user\t/h/.ssh/authorized_keys\t{key}",
        f"me\t{sshkeys.DROPBEAR}\t{key}",
    ):
        assert sshkeys.parse(row)[0] == []
    assert len(sshkeys.parse(f"root\t{sshkeys.DROPBEAR}\t{key}\r")[0]) == 1


async def test_removing_a_crlf_line(host) -> None:
    keys, device, keys_file, other = host
    keys_file.write_text(keys_file.read_text().replace("someone@laptop\n", "someone@laptop\r\n"))
    found, _ = await sshkeys.list_keys(keys, device)
    them = next(k for k in found if k.fingerprint == other.get_fingerprint())
    assert await sshkeys.remove_key(keys, device, ME, them.file, them.fingerprint) == 1
    assert "someone@laptop" not in keys_file.read_text()


async def test_the_switch_resumes_after_a_crash_and_never_loses_a_key(host) -> None:
    keys, device, _, _ = host
    first = keys.hub_key().get_fingerprint()
    rotator = sshkeys.Rotator(keys)
    rot = await rotator.start({"box": device})
    # The process dies right after the key files were switched, before the state was saved.
    keys.promote_next(expect=rot.next)
    rot.stage = "switching"
    rot.save(rotator.path)
    rot = await rotator.finish({"box": device})
    assert rot.stage == "done" and keys.hub_key().get_fingerprint() == rot.next != first
    # A stale old key from somewhere else is never overwritten.
    keys.make_next()
    (keys.folder / "hub_ed25519.old").write_bytes((keys.folder / "hub_ed25519.next").read_bytes())
    with pytest.raises(ValueError, match="old key"):
        keys.promote_next(expect=keys.side_key("next").get_fingerprint())


async def test_a_changed_login_starts_over(host) -> None:
    keys, device, _, _ = host
    rotator = sshkeys.Rotator(keys)
    await rotator.start({"box": device})
    moved = device.model_copy(update={"ssh": device.ssh.model_copy(update={"port": 1})})
    with pytest.raises(ValueError, match="box"):
        await rotator.finish({"box": moved})  # the key went to the old login, not this one


def test_the_collector_and_actor_keys_are_kastellans(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    h = {"X-Kastellan": "1"}
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  box:\n    host: 127.0.0.1\n    ssh:\n      user: me\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('admin pw')}\n"
    )
    keys = Keys(tmp_path / "ssh")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        c.post("/api/auth/login", json={"user": "ada", "password": "admin pw"}, headers=h)
        for which in ("collect", "act"):
            body = {"user": "me", "file": "/x", "fingerprint": keys.hub_key(which).get_fingerprint()}
            r = c.post("/api/devices/box/keys/remove", json=body, headers=h)
            assert r.status_code == 400 and "Kastellan's own" in r.json()["detail"]
