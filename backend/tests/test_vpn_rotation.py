"""Rotating the VPN key against an SSH server that enforces command= as sshd does (the real
kastellan-pki and kastellan-crl run, with SSH_ORIGINAL_COMMAND), and the scripts' key commands."""

import asyncio
import subprocess
from importlib.resources import files
from pathlib import Path

import asyncssh
import pytest

from kastellan.ssh import Keys, Target
from kastellan.vpn.rotation import VpnRotator, _blob

SCRIPTS = files("kastellan.vpn") / "scripts"


def install(tmp_path: Path, name: str, keys_file: Path) -> tuple[Path, Path]:
    """The script and its config (pointing at a test authorized_keys)."""
    script = tmp_path / name
    script.write_text((SCRIPTS / name).read_text())
    script.chmod(0o755)
    conf = tmp_path / f"{name}.conf"
    conf.write_text(f"AUTHORIZED_KEYS={keys_file}\nEASYRSA_DIR={tmp_path}\n")
    return script, conf


def run(script: Path, conf: Path, command: str) -> subprocess.CompletedProcess:
    shell = "bash" if script.name == "kastellan-pki" else "sh"
    env = {"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": command}
    # As sshd runs command="<script> <conf>": $0 is the script's path.
    return subprocess.run([shell, "-c", f'"{script}" "{conf}"'], capture_output=True, text=True, env=env)


def key() -> asyncssh.SSHKey:
    return asyncssh.generate_private_key("ssh-ed25519")


@pytest.mark.parametrize("name", ["kastellan-pki", "kastellan-crl"])
def test_the_scripts_hand_on_only_their_own_power(tmp_path: Path, name: str) -> None:
    keys_file = tmp_path / "authorized_keys"
    script, conf = install(tmp_path, name, keys_file)
    old, new, other = _blob(key()), _blob(key()), _blob(key())
    mine = f'from="10.0.0.2,10.0.0.3",restrict,command="{script} {conf}"'
    keys_file.write_text(
        f"ssh-ed25519 {other} me@laptop\n"  # someone's own key: never touched
        f'command="/usr/bin/other" ssh-ed25519 {old} bound-elsewhere\n'  # old, but not this script's
        f'command="{script}-other" ssh-ed25519 {old} similar-name\n'  # another script, same prefix
        f"{mine}\tssh-ed25519 {old} kastellan-vpn\n"  # a tab before the key: still found by field
    )
    r = run(script, conf, f"add-key {old} {new}")
    assert r.returncode == 0, r.stderr
    lines = keys_file.read_text().splitlines()
    assert lines[-1] == f"{mine} ssh-ed25519 {new} kastellan-vpn" and len(lines) == 5  # options exact
    assert run(script, conf, f"add-key {old} {new}").returncode == 0  # again: nothing more
    assert len(keys_file.read_text().splitlines()) == 5

    # Only ed25519 keys, and nothing but the key: no options sneak in.
    for bad in [f'{new[:-1]}"', "AAAAB3NzaC1yc2EAAAADAQABAAABAQ", f"{new},command=x", "x"]:
        assert run(script, conf, f"add-key {old} {bad}").returncode != 0, bad
    assert run(script, conf, f"add-key {other} {_blob(key())}").returncode != 0  # not its line
    assert run(script, conf, f"keep-key {other}").returncode != 0  # not its line: nothing changes
    assert len(keys_file.read_text().splitlines()) == 5

    # A leaked key handed its power on: keep-key leaves only the new key, whatever else was added.
    attacker = _blob(key())
    assert run(script, conf, f"add-key {old} {attacker}").returncode == 0
    r = run(script, conf, f"keep-key {new}")
    assert r.returncode == 0 and "removed 2 other key line(s)" in r.stdout, r.stdout + r.stderr
    text = keys_file.read_text()
    assert attacker not in text and f"{mine} ssh-ed25519 {new}" in text
    assert "bound-elsewhere" in text and "similar-name" in text and "me@laptop" in text
    assert run(script, conf, f"keep-key {old}").returncode != 0  # gone: it can't come back


@pytest.mark.parametrize("name", ["kastellan-pki", "kastellan-crl"])
def test_the_scripts_take_the_line_as_documented(tmp_path: Path, name: str) -> None:
    """command="<script>" without its config argument (as a hand install may write it)."""
    keys_file = tmp_path / "authorized_keys"
    script, _ = install(tmp_path, name, keys_file)
    old, new = _blob(key()), _blob(key())
    keys_file.write_text(f'command="{script}",no-pty ssh-ed25519 {old} kastellan-vpn')  # no newline
    env = {"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": f"add-key {old} {new}"}
    shell = "bash" if name == "kastellan-pki" else "sh"
    # Its config here only points at the test file; the real one is found the usual way.
    script.write_text(script.read_text().replace("AUTHORIZED_KEYS=  #", f"AUTHORIZED_KEYS={keys_file}  #", 1))
    r = subprocess.run([shell, "-c", f'"{script}"'], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert (
        keys_file.read_text().splitlines()[-1] == f'command="{script}",no-pty ssh-ed25519 {new} kastellan-vpn'
    )


def test_both_scripts_share_one_key_block() -> None:
    """The key commands are the same text in both scripts (one is sh, one bash)."""

    def block(name: str) -> str:
        text = (SCRIPTS / name).read_text()
        return text[text.index("# Rotating Kastellan's VPN key") : text.index('  if [ "$op" = keep ]')]

    assert block("kastellan-pki") == block("kastellan-crl")


@pytest.fixture
async def hosts(tmp_path: Path):
    """Two hosts (the PKI host, the OpenVPN server) on one fake sshd that enforces command=."""
    # Each login has its own authorized_keys, as on the real hosts.
    pki_keys, crl_keys = tmp_path / "pki_keys", tmp_path / "crl_keys"
    pki, pki_conf = install(tmp_path, "kastellan-pki", pki_keys)
    crl, crl_conf = install(tmp_path, "kastellan-crl", crl_keys)
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    class Server(asyncssh.SSHServer):
        options: dict = {}  # noqa: RUF012 - per connection, set at login

        def connection_made(self, conn):
            self.peer = conn.get_extra_info("peername")[0]

        def begin_auth(self, username: str) -> bool:
            return True

        def public_key_auth_supported(self) -> bool:
            return True

        def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
            path = {"kastellan-pki": pki_keys, "root": crl_keys}.get(username)
            try:
                allowed = asyncssh.read_authorized_keys(str(path))
            except (OSError, ValueError, TypeError):
                return False
            options = allowed.validate(key, self.peer, self.peer)
            if options is None:
                return False
            self.options = options
            return True

    async def run_one(process: asyncssh.SSHServerProcess) -> None:
        server = process.get_extra_info("connection").get_owner()
        env = {"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": process.command or ""}
        forced = server.options["command"]  # what sshd runs, whatever was asked
        proc = await asyncio.create_subprocess_exec(
            "sh", "-c", forced, stdin=asyncio.subprocess.DEVNULL, stdout=-1, stderr=-1, env=env
        )
        out, err = await proc.communicate()
        process.stdout.write(out.decode(errors="replace"))
        process.stderr.write(err.decode(errors="replace"))
        process.exit(proc.returncode or 0)

    server = await asyncssh.create_server(
        Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=run_one
    )
    port = server.sockets[0].getsockname()[1]
    keys = Keys(tmp_path / "ssh")
    vpn = keys.hub_key("vpn")
    (tmp_path / "ssh" / "known_hosts").write_text(
        f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n"
    )
    blob = _blob(vpn)
    pki_keys.write_text(
        f"ssh-ed25519 {_blob(asyncssh.generate_private_key('ssh-ed25519'))} someone@laptop\n"
        f'from="127.0.0.1",restrict,command="{pki} {pki_conf}" ssh-ed25519 {blob} kastellan-vpn\n'
    )
    crl_keys.write_text(f'command="{crl} {crl_conf}",no-pty ssh-ed25519 {blob} kastellan-vpn\n')
    targets = {
        "pki": Target("127.0.0.1", port, "kastellan-pki"),
        "server": Target("127.0.0.1", port, "root"),
    }
    yield keys, targets, (pki_keys, crl_keys)
    server.close()


async def test_rotating_the_vpn_key(hosts) -> None:
    keys, targets, (pki_keys, crl_keys) = hosts
    old = keys.hub_key("vpn")
    rotator = VpnRotator(keys)

    rot = await rotator.start(targets)
    assert rot.stage == "adding" and all(s.added for s in rot.devices.values()), rot.devices
    nxt = keys.side_key("next", "vpn")
    assert nxt is not None
    # The new lines carry the old lines' options.
    assert pki_keys.read_text().splitlines()[-1].startswith('from="127.0.0.1",restrict,command="')
    assert crl_keys.read_text().splitlines()[-1].endswith(f",no-pty ssh-ed25519 {_blob(nxt)} kastellan-vpn")

    rot = await rotator.finish(targets)
    assert rot.stage == "done", rot.devices
    assert keys.hub_key("vpn").get_fingerprint() == nxt.get_fingerprint()
    assert keys.side_key("old", "vpn") is None and keys.side_key("next", "vpn") is None
    text = pki_keys.read_text() + crl_keys.read_text()
    assert _blob(old) not in text and text.count(_blob(nxt)) == 2 and "someone@laptop" in text
    assert keys.hub_key().get_fingerprint() != nxt.get_fingerprint()  # the hub key: untouched
    for target in targets.values():  # the old key is refused, the new one works
        with pytest.raises(asyncssh.PermissionDenied):
            async with await keys.connect(target, "vpn", only=old):
                pass
        async with await keys.connect(target, "vpn", only=nxt):
            pass


async def test_cancelling_takes_the_next_key_off_again(hosts) -> None:
    keys, targets, files = hosts
    rotator = VpnRotator(keys)
    await rotator.start(targets)
    nxt = keys.side_key("next", "vpn")
    assert nxt is not None and all(_blob(nxt) in f.read_text() for f in files)
    out = await rotator.cancel(targets)
    assert out.stage == "cancelled" and all(
        "removed 1 other key line" in (x.note or "") for x in out.devices.values()
    )
    assert not any(_blob(nxt) in f.read_text() for f in files)
    assert keys.side_key("next", "vpn") is None and rotator.state() is None


async def test_a_host_that_fails_holds_the_switch(hosts) -> None:
    keys, targets, _ = hosts
    rotator = VpnRotator(keys)
    broken = {**targets, "server": Target("127.0.0.1", targets["server"].port, "nobody")}
    rot = await rotator.start(broken)
    assert rot.devices["pki"].added and not rot.devices["server"].added
    with pytest.raises(ValueError, match="server"):
        await rotator.finish(broken)
    assert keys.side_key("next", "vpn") is not None and rotator.state().stage == "adding"  # not switched


async def test_a_leaked_key_is_gone_after_rotating(hosts) -> None:
    """Whoever held the key may have handed its power on (add-key): rotating ends that too."""
    keys, targets, (pki_keys, crl_keys) = hosts
    attacker = _blob(key())
    current = keys.hub_key("vpn")
    for target in targets.values():
        async with await keys.connect(target, "vpn", only=current) as conn:
            r = await conn.run(f"add-key {_blob(current)} {attacker}", check=False, stdin=asyncssh.DEVNULL)
            assert r.exit_status == 0, r.stderr
    rotator = VpnRotator(keys)
    await rotator.start(targets)
    rot = await rotator.finish(targets)
    assert rot.stage == "done", rot.devices
    assert all("removed 2 other key lines" in (s.note or "") for s in rot.devices.values()), rot.devices
    assert attacker not in pki_keys.read_text() + crl_keys.read_text()


async def test_a_skipped_host_holds_the_end_until_it_works_or_is_forgotten(hosts) -> None:
    keys, targets, (_, crl_keys) = hosts
    old = keys.hub_key("vpn")
    rotator = VpnRotator(keys)
    saved = crl_keys.read_text()
    crl_keys.unlink()  # the server doesn't let anyone in for now
    await rotator.start(targets)
    rot = await rotator.finish(targets, anyway=True)
    # Switched, but not done: the server still trusts the old key, so the old private key stays.
    assert rot.stage == "removing" and rot.devices["pki"].removed
    assert not rot.devices["server"].removed and rot.devices["server"].error
    assert keys.side_key("old", "vpn") is not None
    # It answers again: it gets the new key from the old one, and only that stays.
    crl_keys.write_text(saved)
    rot = await rotator.finish(targets)
    assert rot.stage == "done", rot.devices
    assert _blob(old) not in crl_keys.read_text() and _blob(keys.hub_key("vpn")) in crl_keys.read_text()


async def test_forgetting_a_host_finishes_with_a_warning(hosts) -> None:
    keys, targets, _ = hosts
    rotator = VpnRotator(keys)
    broken = {**targets, "server": Target("127.0.0.1", targets["server"].port, "nobody")}
    await rotator.start(broken)
    rot = await rotator.finish(broken, anyway=True)
    assert rot.stage == "removing"
    with pytest.raises(ValueError):
        await rotator.forget("nope")
    rot = await rotator.forget("server")
    assert rot.stage == "done" and rot.devices["server"].forgotten
    assert "may still be trusted" in (rot.devices["server"].error or "")
    assert keys.side_key("old", "vpn") is None


def test_a_rotation_from_before_several_vpns_carries_on(tmp_path: Path) -> None:
    """Its hosts were "pki" and "server": now those of the VPN `openvpn`, so the end runs there."""
    import json

    keys = Keys(tmp_path)
    rotator = VpnRotator(keys)
    rotator.path.parent.mkdir(parents=True, exist_ok=True)
    rotator.path.write_text(
        json.dumps(
            {
                "started": "2026-11-21T09:00:00+00:00",
                "stage": "removing",
                "devices": {"pki": {"added": True}, "server": {"added": True}},
            }
        )
    )
    rotator.rename_hosts({"pki": "openvpn:pki", "server": "openvpn:server"})
    rot = VpnRotator(keys).state()
    assert rot is not None and set(rot.devices) == {"openvpn:pki", "openvpn:server"}
