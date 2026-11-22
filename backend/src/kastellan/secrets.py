"""Secrets (passwords, tokens) Kastellan uses by itself, by name.

config.yaml names a secret (`password_secret: wg-easy`); the value never sits in config.yaml,
in logs, or in API answers. With a key ring (vault.py) they are its service entries, readable
only while it is unlocked; before one is made, files: <data>/secrets/<name>, mode 0600, set with
`kastellan secret set <name>`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .vault import Vault

NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

# Kastellan's own secrets: made and used by Kastellan itself, never named in config.yaml (no login
# or notification may use them) and never shown or edited on Keys.
HUB_BACKUP_KEY = "hub-backup-key"
DEVICE_BACKUPS_KEY = "device-backups"
FINGERPRINT_SALT = "group-fingerprints"  # keys the password fingerprints Kastellan compares
SYSLOG_CA_KEY = "syslog-ca-key"
OWN = {
    HUB_BACKUP_KEY: "Kastellan's own backup: its key",
    DEVICE_BACKUPS_KEY: "Device backups: their key",
    FINGERPRINT_SALT: "Groups: the key for password fingerprints",
    SYSLOG_CA_KEY: "Syslog over TLS: the certificate authority's key",
}


class Secrets:
    def __init__(self, folder: Path, vault: Vault | None = None) -> None:
        self.folder = folder
        self.vault = vault

    @property
    def in_vault(self) -> bool:
        return self.vault is not None and self.vault.exists

    def _path(self, name: str) -> Path:
        if not NAME.match(name):
            raise ValueError(f"secret names are lowercase letters, digits, . _ -: {name!r}")
        return self.folder / name

    def get(self, name: str) -> str:
        """The secret; KeyError when it isn't set, vault.Locked while the key ring is locked."""
        if self.in_vault:
            assert self.vault is not None
            self._path(name)  # the name checked as always
            try:
                return self.vault.get(name)
            except KeyError as e:
                raise KeyError(f"secret {name!r} is not set (set it in the key ring)") from e
        try:
            return self._path(name).read_text().rstrip("\n")
        except FileNotFoundError as e:
            raise KeyError(f"secret {name!r} is not set (kastellan secret set {name})") from e

    def has(self, name: str) -> bool:
        """Whether it is set (also while the key ring is locked)."""
        if self.in_vault:
            assert self.vault is not None
            return self.vault.has(name)
        try:
            return self._path(name).is_file()
        except ValueError:
            return False

    def set(self, name: str, value: str) -> None:
        path = self._path(name)
        if self.in_vault:
            assert self.vault is not None
            old = self.vault.entry(name)
            self.vault.put(name, value, name=old.name if old else name, notes=old.notes if old else "")
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        if self.folder.is_symlink():
            raise ValueError(f"{self.folder} is a symlink; refusing to write secrets through it")
        os.chmod(self.folder, 0o700)
        tmp = self.folder / f".{name}.{os.getpid()}.tmp"  # never another secret's name
        tmp.unlink(missing_ok=True)
        # New file only (no stale file with other permissions), never through a symlink.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(value)
        tmp.replace(path)

    def names(self) -> list[str]:
        """The secrets kept as files (before a key ring takes them in)."""
        try:
            return sorted(
                p.name for p in self.folder.iterdir() if NAME.match(p.name) and not p.name.endswith(".tmp")
            )
        except OSError:
            return []
