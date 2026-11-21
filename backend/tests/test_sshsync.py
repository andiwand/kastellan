"""SSH files across a group: reading entries the way sshd and ssh do, lining them up, changing
exactly what was asked; and against an SSH server that really runs the commands (test_sshkeys)."""

import asyncssh
import pytest
from test_sshkeys import ME, host  # noqa: F401 (the fixture)

from kastellan import sshkeys, sshsync


def pub(comment: str = "") -> str:
    return asyncssh.generate_private_key("ssh-ed25519", comment=comment).export_public_key().decode().strip()


A, B = pub("ada@laptop"), pub("bob@phone")


def test_reading_entries() -> None:
    e = sshsync.parse_entry("authorized_keys", f'from="10.0.0.1,10.0.0.2",command="echo hi there" {A}')
    assert e is not None and e.type == "ssh-ed25519" and e.label == "ada@laptop"
    assert e.key == " ".join(A.split()[:2]) and e.variant.startswith('from="10.0.0.1,10.0.0.2"')
    for nothing in ("", "  ", "# a comment", "ssh-ed25519 not-base64!", "garbage"):
        assert sshsync.parse_entry("authorized_keys", nothing) is None
    key = A.split()[1]
    known = sshsync.parse_entry("known_hosts", f"nas.lan,10.0.0.2 ssh-ed25519 {key}")
    assert (
        known is not None
        and known.key == "nas.lan,10.0.0.2 ssh-ed25519"
        and known.label == "nas.lan,10.0.0.2"
    )
    hashed = sshsync.parse_entry("known_hosts", f"|1|c2FsdA==|aGFzaA== ssh-ed25519 {key}")
    assert hashed is not None and hashed.label == "hashed"
    ca = sshsync.parse_entry("known_hosts", f"@cert-authority *.lan ssh-ed25519 {key}")
    assert ca is not None and ca.key == "@cert-authority *.lan ssh-ed25519"
    assert sshsync.parse_entry("known_hosts", f"bad host ssh-ed25519 {key}") is None


def test_changing_exactly_what_was_asked() -> None:
    raw = f"# mine\n{A}\n\nno-pty {B}\n".encode()
    a_key = " ".join(A.split()[:2])
    new, added, removed = sshsync.apply("authorized_keys", raw, [], {a_key})
    assert new == f"# mine\n\nno-pty {B}\n".encode() and [e.label for e in removed] == ["ada@laptop"]
    c = pub("cy@desk")
    new, added, removed = sshsync.apply("authorized_keys", raw, [c], set())
    assert new == raw + f"{c}\n".encode() and [e.label for e in added] == ["cy@desk"] and not removed
    assert sshsync.apply("authorized_keys", new, [c], set())[0] == new  # idempotent
    # B with other options: changed where it is, never twice, never lost.
    changed, added, removed = sshsync.apply("authorized_keys", raw, [f"restrict {B}"], set())
    assert changed == raw.replace(b"no-pty ", b"restrict ") and len(added) == len(removed) == 1
    b_key = " ".join(B.split()[:2])
    again, _, _ = sshsync.apply("authorized_keys", raw, [f'from="10.0.0.2" {B}'], {b_key})
    assert f'from="10.0.0.2" {B}'.encode() in again  # removed and added back: changed, not gone
    no_newline = f"{A}".encode()
    assert sshsync.apply("authorized_keys", no_newline, [c], set())[0] == f"{A}\n{c}\n".encode()
    odd = b"\xff not utf-8\n" + raw
    assert sshsync.apply("authorized_keys", odd, [c], set())[0].startswith(b"\xff not utf-8\n")
    for bad in (f"{A}\n{B}", "ssh-ed25519", "hello"):
        with pytest.raises(ValueError):
            sshsync.check_line("authorized_keys", bad)


def test_lining_up() -> None:
    key_a, key_b = A.split()[1], B.split()[1]
    copies = {
        "nas/root": sshsync.read_output(
            "known_hosts", f"/root/.ssh/known_hosts\tyes\nnas ssh-ed25519 {key_a}\n".encode()
        ),
        "pi/ada": sshsync.read_output(
            "known_hosts", f"/home/ada/.ssh/known_hosts\tyes\nnas ssh-ed25519 {key_b}\n".encode()
        ),
        "box/ada": sshsync.read_output("known_hosts", b"/home/ada/.ssh/known_hosts\tno\n"),
    }
    assert copies["box/ada"].version == "none" and not copies["box/ada"].exists
    (row,) = sshsync.merge("known_hosts", copies, {})
    assert row["conflict"] and set(row["on"]) == {"nas/root", "pi/ada"}
    keys = {
        "x/root": sshsync.read_output("authorized_keys", f"f\tyes\n{A}\nno-pty {B}\n".encode()),
        "y/root": sshsync.read_output("authorized_keys", f"f\tyes\n{A}\n{B}\n".encode()),
        "z/root": sshsync.read_output("authorized_keys", f"f\tyes\n{B}\n".encode()),
    }
    fp_a = asyncssh.import_public_key(A).get_fingerprint()
    rows = {r["label"]: r for r in sshsync.merge("authorized_keys", keys, {fp_a: "Kastellan"})}
    assert rows["ada@laptop"]["kastellan"] == "Kastellan" and set(rows["ada@laptop"]["on"]) == {
        "x/root",
        "y/root",
    }
    assert rows["bob@phone"]["on"] == {"x/root": "differs", "y/root": "same", "z/root": "same"}
    assert rows["bob@phone"]["line"] == B and not rows["bob@phone"]["conflict"]


async def test_fetch_and_push_on_a_device(host) -> None:  # noqa: F811
    keys, device, keys_file, other = host
    got = await sshsync.fetch(keys, device, "authorized_keys", [sshsync.LOGIN, "no-such-user-here", "root"])
    copy = got[ME]
    assert isinstance(copy, sshsync.Copy) and copy.exists and len(copy.entries) == 2
    assert isinstance(got["no-such-user-here"], LookupError)
    assert isinstance(got["root"], PermissionError)  # not root, no sudo: only the login's own

    new = pub("new@laptop")
    other_key = " ".join(other.export_public_key().decode().split()[:2])
    added, removed = await sshsync.push(keys, device, "authorized_keys", ME, copy.version, [new], {other_key})
    assert [e.label for e in added] == ["new@laptop"] and [e.label for e in removed] == ["someone@laptop"]
    text = keys_file.read_text()
    assert "# a comment" in text and "kastellan-hub" in text and "new@laptop" in text
    assert "someone@laptop" not in text
    # Still Kastellan's way in afterwards; and a stale version is refused.
    assert (await sshkeys.list_keys(keys, device))[0]
    with pytest.raises(RuntimeError, match="fetch again"):
        await sshsync.push(keys, device, "authorized_keys", ME, copy.version, [pub()], set())

    # known_hosts that isn't there yet: made (the folder exists), then found.
    hosts = await sshsync.fetch(keys, device, "known_hosts", [])
    assert hosts[ME].version == "none"
    line = f"nas.lan ssh-ed25519 {new.split()[1]}"
    await sshsync.push(keys, device, "known_hosts", ME, "none", [line], set())
    assert (keys_file.parent / "known_hosts").read_text() == line + "\n"


def test_the_routes_refuse_what_they_must(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n    ssh: {user: ada}\n  cam:\n    host: 127.0.0.2\n"
        "groups:\n  home:\n    devices: [nas, cam]\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('admin password')}\n"
        f"  obi:\n    role: observer\n    password: {hash_password('observer password')}\n"
    )
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        signin = {"user": "obi", "password": "observer password"}
        assert c.post("/api/auth/login", json=signin, headers=h).status_code == 200
        assert c.get("/api/groups/home/ssh").status_code == 403
        assert c.post(
            "/api/auth/login", json={**signin, "user": "ada", "password": "admin password"}, headers=h
        )
        assert c.get("/api/groups").json()[0]["ssh"] == ["nas"]
        own = c.app.state.hub.keys.public_key()
        base = {"file": "authorized_keys"}

        def push(changes: dict) -> int:
            return c.post(
                "/api/groups/home/ssh/push", json=base | {"changes": changes}, headers=h
            ).status_code

        own_key = " ".join(own.split()[:2])
        assert push({"nas/ada": {"version": "x", "remove": [own_key]}}) == 400  # Kastellan's own
        assert push({"nas/ada": {"version": "x", "add": [own_key]}}) == 400  # only through its setup
        assert push({"cam/ada": {"version": "x", "add": [A]}}) == 400  # no ssh: there
        assert push({"nas/ada": {"version": "x", "add": ["not a key"]}}) == 400
        assert push({"nas/ada": {"version": "x"}}) == 400  # nothing to change
        bad_user = c.get("/api/groups/home/ssh", params={"users": ["a b"]})
        assert bad_user.status_code == 400
