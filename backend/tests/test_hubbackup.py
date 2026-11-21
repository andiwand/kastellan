from __future__ import annotations

import base64
from pathlib import Path

import pytest

from kastellan import hubbackup


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
    config.write_text("devices:\n  sv1:\n    host: sv1.lan\nusers:\n  ada:\n    password: scrypt$secret\n")
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
    config.write_text(config.read_text() + "  bob:\n    password: scrypt$other\n")
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
