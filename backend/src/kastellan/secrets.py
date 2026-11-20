"""Secrets (passwords, tokens) as files: <data>/secrets/<name>, mode 0600, one secret each.

config.yaml names a secret (`password_secret: wg-easy`); the value never sits in config.yaml,
in logs, or in API answers. Set one with `kastellan secret set <name>` (asks for it).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class Secrets:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _path(self, name: str) -> Path:
        if not NAME.match(name):
            raise ValueError(f"secret names are lowercase letters, digits, . _ -: {name!r}")
        return self.folder / name

    def get(self, name: str) -> str:
        """The secret; KeyError when it isn't set."""
        try:
            return self._path(name).read_text().rstrip("\n")
        except FileNotFoundError as e:
            raise KeyError(f"secret {name!r} is not set (kastellan secret set {name})") from e

    def set(self, name: str, value: str) -> None:
        path = self._path(name)
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
        try:
            return sorted(
                p.name for p in self.folder.iterdir() if NAME.match(p.name) and not p.name.endswith(".tmp")
            )
        except OSError:
            return []
