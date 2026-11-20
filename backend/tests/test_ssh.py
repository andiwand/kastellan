from pathlib import Path

import asyncssh
import pytest

from kastellan.ssh import ChangedHost, Keys, Target, UnknownHost


async def start_server(host_key: asyncssh.SSHKey, client_pub: str):
    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

    authorized = asyncssh.import_authorized_keys(client_pub)

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        process.stdout.write(f"hello {process.get_extra_info('username')}\n")
        process.exit(0)

    server = await asyncssh.create_server(
        Server,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=authorized,
        process_factory=handle,
    )
    port = server.sockets[0].getsockname()[1]
    return server, Target("127.0.0.1", port, "root")


async def test_pin_trust_connect_and_changed_key(tmp_path: Path) -> None:
    keys = Keys(tmp_path / "ssh")
    pub = keys.public_key()
    assert pub.startswith("ssh-ed25519 ") and (tmp_path / "ssh" / "hub_ed25519").stat().st_mode & 0o077 == 0

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    server, target = await start_server(host_key, pub)
    try:
        with pytest.raises(UnknownHost) as unknown:
            await keys.connect(target)  # never trusted by itself
        assert unknown.value.fingerprint == host_key.get_fingerprint()

        with pytest.raises(ChangedHost):
            await keys.trust(target, "SHA256:something-else")  # not what the admin saw
        await keys.trust(target, unknown.value.fingerprint)
        async with await keys.connect(target) as conn:
            result = await conn.run("anything")
        assert result.stdout == "hello root\n"
    finally:
        server.close()


async def test_changed_host_key_is_refused_until_forgotten(tmp_path: Path) -> None:
    keys = Keys(tmp_path / "ssh")
    old_key = asyncssh.generate_private_key("ssh-ed25519")
    new_key = asyncssh.generate_private_key("ssh-ed25519")
    server, target = await start_server(new_key, keys.public_key())
    try:
        # What we pinned earlier for this address is the old key; the host now shows a new one.
        keys.known_hosts.write_text(f"{target.name} {old_key.export_public_key().decode().strip()}\n")
        with pytest.raises(ChangedHost):
            await keys.connect(target)
        with pytest.raises(ChangedHost):
            await keys.trust(target, new_key.get_fingerprint())  # replacing needs `forget` first
        await keys.forget(target)
        await keys.trust(target, new_key.get_fingerprint())
        async with await keys.connect(target) as conn:
            assert (await conn.run("x")).exit_status == 0
    finally:
        server.close()
