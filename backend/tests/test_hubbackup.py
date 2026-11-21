from __future__ import annotations

import base64
from pathlib import Path

import pytest

from kastellan import hubbackup
from kastellan.auth import hash_password


def hub_data(root: Path) -> tuple[Path, Path]:
    data, config = root / "data", root / "config" / "config.yaml"
    for rel, text in {
        "vault.json": '{"rings": {}}',
        "ssh/hub_ed25519": "PRIVATE KEY",
        "ssh/known_hosts": "sv1 ssh-ed25519 AAAA",
        "secrets/wg-easy": "pw",
        "sessions.json": "{}",
        "checks/2026-11-21.csv": "t,device\n",
        "logs.db": "SQLite",
        "vulns/cache.json": "{}",
        "device-backups/objects/ab/abc": "sealed",
    }.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(text)
    config.parent.mkdir(parents=True)
    config.write_text(
        "devices:\n  sv1:\n    host: sv1.lan\nusers:\n  ada:\n"
        f"    password: {hash_password('test-password')}\n"
    )
    return data, config


@pytest.fixture(autouse=True)
def _quick_scrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    monkeypatch.setattr(
        hubbackup,
        "_scrypt",
        lambda p, salt: Scrypt(salt=salt, length=32, n=2**10, r=8, p=1).derive(
            "".join(p.split()).replace("-", "").upper().encode()
        ),
    )


def test_back_up_copy_and_restore_into_a_new_hub(tmp_path: Path) -> None:
    data, config = hub_data(tmp_path / "old")
    phrase = hubbackup.new_passphrase()
    made = hubbackup.make_key(phrase)
    key = base64.b64decode(made["key"])
    hb = hubbackup.HubBackup(data, lambda: key)
    hb.start(made)
    first, new = hb.take(data, config, keep=10, history=True, logs=False)
    assert new
    paths = {f.path for f in first.files}
    assert paths == {
        "@config/config.yaml",
        "vault.json",
        "ssh/hub_ed25519",
        "ssh/known_hosts",
        "secrets/wg-easy",
        "checks/2026-11-21.csv",
        "device-backups/objects/ab/abc",
    }  # no sessions, logs, caches, nor the backup itself
    again, new = hb.take(data, config, keep=10, history=True, logs=False)
    assert not new and again.id == first.id
    config.write_text(config.read_text() + f"  bob:\n    password: {hash_password('another-password')}\n")
    second, new = hb.take(data, config, keep=10, history=True, logs=False)
    assert new
    (changed,) = hb.store.diff(hubbackup.ID, first.id, second.id, b"salt")["changed"]
    assert changed["path"] == "@config/config.yaml" and "scrypt$other" not in "\n".join(changed["diff"])

    target = tmp_path / "nas"
    assert hubbackup.copy_tree(hb.folder, target) > 0
    assert hubbackup.copy_tree(hb.folder, target) == 0  # nothing new to copy
    stored = b"".join(p.read_bytes() for p in target.rglob("*") if p.is_file())
    assert b"PRIVATE KEY" not in stored and b"scrypt$secret" not in stored

    new_data, new_config = tmp_path / "new" / "data", tmp_path / "new" / "config.yaml"
    with pytest.raises(ValueError, match="passphrase"):
        hubbackup.restore(target, "WRONG-PASSPHRASE", new_data, new_config)
    done = hubbackup.restore(target, phrase.lower().replace("-", " "), new_data, new_config)
    assert done["version"] == second.id and done["files"] == 7
    assert new_config.read_text() == config.read_text()
    assert (new_data / "ssh/hub_ed25519").read_text() == "PRIVATE KEY"
    assert not (new_data / "sessions.json").exists()
    with pytest.raises(ValueError, match="already holds"):
        hubbackup.restore(target, phrase, new_data, new_config)
    older = hubbackup.restore(target, phrase, tmp_path / "older", tmp_path / "older.yaml", version=first.id)
    assert older["version"] == first.id and "bob" not in (tmp_path / "older.yaml").read_text()

    # The single-file download restores the same.
    blob = hubbackup.encrypted_archive(hb.store, hb.header(), second.id, key)
    assert b"PRIVATE KEY" not in blob
    (tmp_path / "x.kbak").write_bytes(blob)
    out = hubbackup.restore_file(
        tmp_path / "x.kbak", phrase, tmp_path / "file" / "data", tmp_path / "file.yaml"
    )
    assert out["files"] == 7 and (tmp_path / "file" / "data" / "vault.json").exists()


def test_a_new_passphrase_starts_a_new_store(tmp_path: Path) -> None:
    data, config = hub_data(tmp_path)
    one = hubbackup.make_key("A")
    hb = hubbackup.HubBackup(data, lambda: base64.b64decode(one["key"]))
    hb.start(one)
    hb.take(data, config, keep=10, history=False, logs=False)
    two = hubbackup.make_key("B")
    hb.start(two)
    assert hb.header()["check"] == two["check"]
    aside = [p for p in tmp_path.joinpath("data").iterdir() if p.name.startswith("hub-backup-")]
    assert len(aside) == 1 and (aside[0] / hubbackup.HEADER).exists()  # the old one, still openable
    assert hubbackup.key_from(hb.header(), "B") == base64.b64decode(two["key"])


def prepared(root):
    data, config = hub_data(root)
    made = hubbackup.make_key("test passphrase")
    key = base64.b64decode(made["key"])
    hb = hubbackup.HubBackup(data, lambda: key)
    hb.start(made)
    snap, _ = hb.take(data, config, 2, False, False)
    return hb, snap, made, data, config


def test_copy_refuses_unrelated_destinations_and_preserves_unrelated_files(tmp_path):
    hb, _, _, _, _ = prepared(tmp_path / "source")
    target = tmp_path / "copy"
    target.mkdir()
    (target / "precious").write_text("keep")
    with pytest.raises(ValueError, match="unrelated"):
        hubbackup.copy_tree(hb.folder, target)
    assert (target / "precious").read_text() == "keep"
    (target / "precious").unlink()
    hubbackup.copy_tree(hb.folder, target)
    (target / "precious").write_text("keep")
    hubbackup.copy_tree(hb.folder, target)
    assert (target / "precious").read_text() == "keep"
    with pytest.raises(ValueError, match="overlap"):
        hubbackup.copy_tree(hb.folder, hb.folder / "copy")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        hubbackup.copy_tree(hb.folder, link)


def test_rotation_copies_do_not_reuse_ciphertext_and_retention_works(tmp_path):
    hb, _snap, _made, data, config = prepared(tmp_path / "source")
    first = Path(hubbackup.copy_path(str(tmp_path / "copies"), hb.header()))
    hubbackup.copy_tree(hb.folder, first)
    next_key = hubbackup.make_key("different phrase")
    hb.start(next_key)
    hb.store._key = lambda: base64.b64decode(next_key["key"])
    for n in range(4):
        (data / "secrets" / "wg-easy").write_text(str(n))
        hb.take(data, config, 2, False, False)
    assert len(hb.store.snapshots(hubbackup.ID)) == 2
    second = Path(hubbackup.copy_path(str(tmp_path / "copies"), hb.header()))
    assert first != second
    hubbackup.copy_tree(hb.folder, second)
    hubbackup.restore(first, "test passphrase", tmp_path / "old", tmp_path / "old.yaml")
    hubbackup.restore(second, "different phrase", tmp_path / "new", tmp_path / "new.yaml")
    assert (tmp_path / "old/secrets/wg-easy").read_text() == "pw"
    assert (tmp_path / "new/secrets/wg-easy").read_text() == "3"


def test_corruption_is_detected_before_changing_the_destination(tmp_path):
    from cryptography.exceptions import InvalidTag

    hb, snap, _, _, _ = prepared(tmp_path / "source")
    target, config = tmp_path / "destination", tmp_path / "config.yaml"
    target.mkdir()
    (target / "sessions.json").write_text("old sessions")
    config.write_text("old config")
    obj = hb.store._object(snap.files[-1].sha)
    obj.write_bytes(b"x" * 100)
    with pytest.raises(InvalidTag):
        hubbackup.restore(hb.folder, "test passphrase", target, config, force=True)
    assert config.read_text() == "old config"
    assert (target / "sessions.json").read_text() == "old sessions"
    index = hb.folder / "kastellan.json"
    raw = index.read_bytes()
    index.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(InvalidTag):
        hubbackup.restore(hb.folder, "test passphrase", target, config, force=True)


@pytest.mark.parametrize(
    "names",
    [
        ["../escape"],
        ["/absolute"],
        ["@config/other.yaml"],
        ["ssh", "ssh/key"],
        ["ssh/key", "ssh"],
        ["ssh/key", "ssh/key"],
        ["sessions.json"],
        ["./file"],
        ["file//other"],
    ],
)
def test_unsafe_members_never_change_existing_state(tmp_path, names):
    target = tmp_path / "data"
    target.mkdir()
    (target / "important").write_text("keep")
    with pytest.raises(ValueError):
        hubbackup.restore_members([(n, b"content") for n in names], target, tmp_path / "config", True)
    assert [p.name for p in target.iterdir()] == ["important"]


def test_force_replaces_stale_secrets_and_sessions_and_private_modes(tmp_path):
    hb, _, _, _, config = prepared(tmp_path / "source")
    target = tmp_path / "data"
    target.mkdir()
    (target / "sessions.json").write_text("stale")
    (target / "old-secret").write_text("stale")
    out_config = tmp_path / "restored.yaml"
    with pytest.raises(ValueError, match="already holds"):
        hubbackup.restore(hb.folder, "test passphrase", target, out_config)
    hubbackup.restore(hb.folder, "test passphrase", target, out_config, force=True)
    assert not (target / "sessions.json").exists() and not (target / "old-secret").exists()
    assert out_config.read_bytes() == config.read_bytes()
    assert (target / "ssh/hub_ed25519").stat().st_mode & 0o777 == 0o600


def test_sqlite_wal_is_backed_up_as_one_consistent_database(tmp_path):
    import sqlite3

    data, config = hub_data(tmp_path)
    (data / "logs.db").unlink()
    with sqlite3.connect(data / "logs.db") as db:
        db.execute("pragma journal_mode=wal")
        db.execute("create table lines (message text)")
        db.execute("insert into lines values ('committed in WAL')")
        db.commit()
        members = dict((f.path, content) for f, content in hubbackup.collect(data, config, False, True))
        assert "logs.db-wal" not in members and "logs.db-shm" not in members
        (tmp_path / "snapshot.db").write_bytes(members["logs.db"])
        with sqlite3.connect(tmp_path / "snapshot.db") as restored:
            assert restored.execute("select message from lines").fetchone()[0] == "committed in WAL"
    config.unlink()
    with pytest.raises(FileNotFoundError):
        hubbackup.collect(data, config, False, False)


async def test_sftp_copy_round_trip(tmp_path):
    import asyncssh

    hb, _, _, _, _ = prepared(tmp_path / "source")
    remote = tmp_path / "remote"
    remote.mkdir()

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return False

    async with (
        asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")],
            sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=str(remote)),
        ) as server,
        asyncssh.connect("127.0.0.1", server.get_port(), known_hosts=None) as conn,
    ):
        assert await hubbackup.copy_sftp(hb.folder, conn, "/backup") > 0
        assert await hubbackup.copy_sftp(hb.folder, conn, "/backup") == 0
        (remote / "backup/keep").write_text("unrelated")
        await hubbackup.copy_sftp(hb.folder, conn, "/backup")
        assert (remote / "backup/keep").read_text() == "unrelated"
        (remote / "linked").symlink_to(remote / "backup", target_is_directory=True)
        with pytest.raises(ValueError, match="symlink"):
            await hubbackup.copy_sftp(hb.folder, conn, "/linked")
    hubbackup.restore(remote / "backup", "test passphrase", tmp_path / "restored", tmp_path / "restored.yaml")


def test_backup_api_access_rotation_and_recovery(tmp_path):
    import json
    from concurrent.futures import ThreadPoolExecutor

    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.config import Config
    from kastellan.settings import Settings
    from kastellan.vault_api import own

    data = tmp_path / "data"
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices: {}\ninternet: {enabled: false}\ncheck_releases: false\n"
        f"hub_backup:\n  targets:\n    - path: {tmp_path / 'copies'}\n"
        f"users:\n  admin: {{password: {hash_password('password')}, role: admin}}\n"
        f"  viewer: {{password: {hash_password('password')}, role: observer}}\n"
    )
    headers = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=data))) as client:
        assert client.post("/api/hub-backup", headers=headers).status_code == 401
        client.post("/api/auth/login", json={"user": "viewer", "password": "password"}, headers=headers)
        assert client.get("/api/hub-backup").status_code == 403
        assert client.post("/api/hub-backup/passphrase", json={}, headers=headers).status_code == 403
        client.post("/api/auth/login", json={"user": "admin", "password": "password"}, headers=headers)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: client.post("/api/hub-backup/passphrase", json={}, headers=headers), range(2)
                )
            )
        assert sorted(r.status_code for r in results) == [200, 409]
        response = next(r for r in results if r.status_code == 200)
        phrase = response.json()["passphrase"]
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/hub-backup/check", json={"passphrase": phrase}, headers=headers).json()[
            "right"
        ]
        done = client.post("/api/hub-backup", headers=headers)
        assert done.status_code == 200, done.text
        status = done.json()
        assert status["ok"] and not status["error"]
        assert Path(next(iter(status["targets"].values()))["path"]).is_dir()
        view = client.get("/api/hub-backup").json()
        assert view["checks"]["passphrase"] and view["versions"]
        sid = view["versions"][0]["id"]
        download = client.get(f"/api/hub-backup/{sid}/download")
        assert download.status_code == 200 and download.headers["cache-control"] == "no-store"
        archive = tmp_path / "restore.kbak"
        archive.write_bytes(download.content)
        hubbackup.restore_file(archive, phrase, tmp_path / "restored", tmp_path / "restored.yaml")
        assert not (tmp_path / "restored/sessions.json").exists()
        assert (
            client.post("/api/hub-backup/passphrase", json={"replace": True}, headers=headers).status_code
            == 200
        )
        assert "passphrase" not in client.get("/api/hub-backup").json()["checks"]
        assert phrase not in (data / "audit.log").read_text()
    assert own("hub-backup-key")
    with pytest.raises(ValueError):
        Config.model_validate(
            {"devices": {"router": {"host": "router", "openwrt": {"password_secret": "hub-backup-key"}}}}
        )
    with pytest.raises(ValueError):
        Config.model_validate(
            {
                "devices": {},
                "vpns": {
                    "wg": {
                        "kind": "wireguard",
                        "wg_easy": "http://router",
                        "password_secret": "hub-backup-key",
                    }
                },
            }
        )
    assert "key" in json.loads((data / "secrets/hub-backup-key").read_text())


def test_copy_failures_keep_the_last_success_for_alarms(tmp_path):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from kastellan.config import HubBackupSettings

    hb, _, _, _, _ = prepared(tmp_path / "source")
    hb.status = {"ok": datetime.fromtimestamp(100, UTC).isoformat(), "tried": 200_000, "error": "copy failed"}
    hub = SimpleNamespace(hub_backup=hb, config=SimpleNamespace(hub_backup=HubBackupSettings()), demo=False)
    assert hubbackup.problems(hub, 200_000)
    hb.status = {"ok": datetime.fromtimestamp(199_999, UTC).isoformat(), "error": None}
    assert not hubbackup.problems(hub, 200_000)


def test_cli_list_on_a_file_never_restores(tmp_path, monkeypatch):
    from kastellan.__main__ import restore_command

    source = tmp_path / "backup.kbak"
    source.write_bytes(b"anything")
    monkeypatch.setenv("KASTELLAN_CONFIG", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("KASTELLAN_DATA", str(tmp_path / "data"))
    with pytest.raises(SystemExit, match="nothing restored"):
        restore_command(str(source), None, True, True)
    assert not (tmp_path / "data").exists()


def test_restore_rolls_back_write_failure_on_the_destination_filesystem(tmp_path, monkeypatch):
    from kastellan import config_edit

    hb, _, _, _, _ = prepared(tmp_path / "source")
    target = tmp_path / "mounted-data"
    target.mkdir()
    (target / "sessions.json").write_text("old sessions")
    (target / "config-backups").mkdir()
    (target / "config-backups/old.yaml").write_text("old backup")
    config = tmp_path / "config.yaml"
    config.write_text("old config")
    original = Path.rename

    def rename(path, destination):
        if path.is_relative_to(target):
            assert Path(destination).is_relative_to(target), "rename would cross the data mount"
        return original(path, destination)

    def failed_save(*args):
        (target / "config-backups").mkdir()
        (target / "config-backups/new.yaml").write_text("new backup")
        raise OSError("disk full")

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(config_edit, "write", failed_save)
    with pytest.raises(OSError, match="disk full"):
        hubbackup.restore(hb.folder, "test passphrase", target, config, force=True)
    assert config.read_text() == "old config"
    assert (target / "sessions.json").read_text() == "old sessions"
    assert list((target / "config-backups").iterdir()) == [target / "config-backups/old.yaml"]


def test_recovery_checks_expire_when_the_key_changes(tmp_path):
    from kastellan.vault import Vault

    vault = Vault(tmp_path / "vault.json")
    keys = vault.create("admin", "password", "tag")
    before = vault.recovery_tag("kastellan")
    vault.lock()
    assert vault.check_recovery("kastellan", keys["kastellan"])
    assert not vault.check_recovery("kastellan", "wrong")
    assert vault.state == "locked"
    vault.unlock_recovery("kastellan", keys["kastellan"])
    newer = vault.new_recovery("kastellan")
    assert before != vault.recovery_tag("kastellan")
    vault.lock()
    assert not vault.check_recovery("kastellan", keys["kastellan"])
    assert vault.check_recovery("kastellan", newer)
    assert vault.state == "locked"


async def test_cancelled_file_work_finishes_before_unlocking():
    import asyncio
    import threading

    started, finish = threading.Event(), threading.Event()
    lock = asyncio.Lock()

    def work():
        started.set()
        assert finish.wait(5)

    async def operation():
        async with lock:
            await hubbackup.blocking(work)

    task = asyncio.create_task(operation())
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not lock.locked()


def test_symlinked_state_is_reported_instead_of_silently_omitted(tmp_path):
    hb, _, _, data, config = prepared(tmp_path / "source")
    target = tmp_path / "external-key"
    target.write_text("private key")
    (data / "ssh/external").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        hb.take(data, config, 2, False, False)


def test_a_backup_that_opens_with_the_key_rings(tmp_path: Path) -> None:
    import json

    from kastellan.vault import KASTELLAN, Vault

    data, config = hub_data(tmp_path / "old")
    (data / "vault.json").unlink()
    vault = Vault(data / "vault.json")
    recovery = vault.create("ada", "ada's password", "tag")[KASTELLAN]
    made = hubbackup.make_keyring_key()
    vault.put(hubbackup.KEY_SECRET, json.dumps({"salt": "", "key": made["key"]}), ring=KASTELLAN)
    key = base64.b64decode(made["key"])
    hb = hubbackup.HubBackup(data, lambda: key)
    hb.start(made)
    assert hb.header()["keyring"] and "salt" not in hb.header()
    snap, _ = hb.take(data, config, keep=10, history=True, logs=False)
    assert (hb.folder / hubbackup.KEYRING).read_bytes() == (data / "vault.json").read_bytes()
    target = tmp_path / "nas"
    hubbackup.copy_tree(hb.folder, target)
    assert (target / hubbackup.KEYRING).exists()

    with pytest.raises(ValueError):
        hubbackup.restore(target, "", tmp_path / "x", tmp_path / "x.yaml", user="ada", password="wrong")
    with pytest.raises(ValueError):  # a passphrase is no way into it
        hubbackup.restore(target, "ANY-PASSPHRASE", tmp_path / "y", tmp_path / "y.yaml")
    done = hubbackup.restore(
        target, "", tmp_path / "a", tmp_path / "a.yaml", user="ada", password="ada's password"
    )
    assert done["version"] == snap.id and (tmp_path / "a.yaml").read_text() == config.read_text()
    done = hubbackup.restore(target, "", tmp_path / "r", tmp_path / "r.yaml", recovery=recovery)
    assert (tmp_path / "r" / "ssh" / "hub_ed25519").read_text() == "PRIVATE KEY"

    blob = hubbackup.encrypted_archive(hb.store, hb.header(), snap.id, key)
    (tmp_path / "k.kbak").write_bytes(blob)
    out = hubbackup.restore_file(
        tmp_path / "k.kbak", "", tmp_path / "f", tmp_path / "f.yaml", user="ada", password="ada's password"
    )
    assert out["files"] >= 7

    # An older download carries that snapshot's ring, even after recovery credentials change.
    newer_recovery = vault.new_recovery(KASTELLAN)
    saved_ring = (hb.folder / hubbackup.KEYRING).read_bytes()
    with pytest.raises(FileNotFoundError):
        hb.take(data, config.with_name("missing.yaml"), keep=10, history=True, logs=False)
    assert (hb.folder / hubbackup.KEYRING).read_bytes() == saved_ring
    newer, _ = hb.take(data, config, keep=10, history=True, logs=False)
    assert newer.id != snap.id
    (tmp_path / "older.kbak").write_bytes(hubbackup.encrypted_archive(hb.store, hb.header(), snap.id, key))
    with pytest.raises(ValueError):
        hubbackup.restore_file(
            tmp_path / "older.kbak", "", tmp_path / "bad", tmp_path / "bad.yaml", recovery=newer_recovery
        )
    assert hubbackup.restore_file(
        tmp_path / "older.kbak", "", tmp_path / "older", tmp_path / "older.yaml", recovery=recovery
    )
    # A copied folder uses the newest snapshot's ring, including when selecting an old version.
    hubbackup.copy_tree(hb.folder, target)
    assert hubbackup.restore(
        target,
        "",
        tmp_path / "old-version",
        tmp_path / "old-version.yaml",
        version=snap.id,
        recovery=newer_recovery,
    )


def test_recovery_check_opens_the_saved_backup_not_the_live_ring(tmp_path):
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import password_tag
    from kastellan.settings import Settings

    hashed = hash_password("password")
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices: {}\ninternet: {enabled: false}\ncheck_releases: false\n"
        f"users:\n  admin: {{password: {hashed}, role: admin}}\n"
    )
    headers = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path / "data"))) as client:
        client.post("/api/auth/login", json={"user": "admin", "password": "password"}, headers=headers)
        vault = client.app.state.hub.vault
        old_key = vault.create("admin", "password", password_tag(hashed))["kastellan"]
        made = client.post("/api/hub-backup/passphrase", json={"mode": "keyring"}, headers=headers)
        assert made.status_code == 200, made.text
        assert made.json()["passphrase"] is None
        body = {"ring": "kastellan", "recovery": old_key}
        assert client.post("/api/hub-backup/check", json=body, headers=headers).status_code == 409
        assert client.post("/api/hub-backup", headers=headers).json()["local_ok"]
        new_key = vault.new_recovery("kastellan")
        assert client.post("/api/hub-backup/check", json=body, headers=headers).json()["right"]
        assert "recovery:kastellan" in client.get("/api/hub-backup").json()["checks"]
        newer = {"ring": "kastellan", "recovery": new_key}
        assert not client.post("/api/hub-backup/check", json=newer, headers=headers).json()["right"]
        assert client.post("/api/hub-backup", headers=headers).json()["local_ok"]
        assert "recovery:kastellan" not in client.get("/api/hub-backup").json()["checks"]
        assert client.post("/api/hub-backup/check", json=newer, headers=headers).json()["right"]
