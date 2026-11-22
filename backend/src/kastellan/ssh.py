"""SSH from the hub: its own keys, pinned host keys, and connections to devices.

- The hub has four ed25519 keys, each made on first use (`Keys.NAMES`): `hub` (terminals,
  setups, reading keys), `vpn` (only the restricted VPN scripts), `collect` (the read-only
  collector) and `act` (the actor's abilities). Without a key ring they are files in
  <data>/ssh (0600, the hub key with a .pub); with one, hub, vpn and act live in the
  `kastellan` ring (unusable while it is locked) and only collect stays a file.
- Host keys are pinned in <data>/ssh/known_hosts. A host we have never seen is not trusted by
  itself: the connection fails with `UnknownHost` (with the fingerprint), and an admin confirms
  it in the app (`trust`). A host whose key changed fails with `ChangedHost`: that is either a
  reinstall or someone in between, and only a person can tell.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import asyncssh

from .vault import Vault

log = logging.getLogger(__name__)

# asyncssh asks for the local user's name on every connection, even with a login given. A
# container running as a uid without a passwd entry has none: Python 3.13 raises OSError there,
# which asyncssh doesn't expect, and every login failed ("No username set in the environment").
os.environ.setdefault("LOGNAME", "kastellan")

CONNECT_TIMEOUT = 10


class UnknownHost(Exception):
    def __init__(self, host: str, fingerprint: str) -> None:
        super().__init__(f"{host} is not trusted yet; its key is {fingerprint}")
        self.host, self.fingerprint = host, fingerprint


class ChangedHost(Exception):
    def __init__(self, host: str, fingerprint: str) -> None:
        super().__init__(f"the key of {host} changed (now {fingerprint}); not connecting")
        self.host, self.fingerprint = host, fingerprint


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    user: str

    @property
    def name(self) -> str:
        """How known_hosts names it: host, or [host]:port."""
        return self.host if self.port == 22 else f"[{self.host}]:{self.port}"


_make_lock = threading.Lock()


class Keys:
    # The hub's keys: "hub" for terminals and installs, "vpn" only for the restricted VPN scripts
    # (bound with command= where they are installed; separate so binding it takes nothing away).
    NAMES: ClassVar[dict[str, str]] = {
        "hub": "hub_ed25519",
        "vpn": "vpn_ed25519",
        "collect": "collect_ed25519",  # the read-only collector (host/collector.py)
        "act": "act_ed25519",  # the actor's abilities (host/actor.py)
    }
    # With a key ring, the keys that can change things live in it (locked: unusable); the
    # collector's read-only key stays a file, so host health goes on while it is locked.
    IN_VAULT: ClassVar[tuple[str, ...]] = ("hub", "vpn", "act")

    def __init__(self, folder: Path, vault: Vault | None = None) -> None:
        self.folder = folder
        self.vault = vault
        self.known_hosts = folder / "known_hosts"
        self._lock = asyncio.Lock()

    # Where a key is kept: `of` (hub, vpn, ...), `side` "" for the key, "next" or "old".
    def _vaulted(self, of: str) -> bool:
        return self.vault is not None and self.vault.exists and of in self.IN_VAULT

    def _file(self, of: str, side: str) -> Path:
        return self.folder / (self.NAMES[of] + (f".{side}" if side else ""))

    @staticmethod
    def vault_id(of: str, side: str = "") -> str:
        return f"ssh/{of}" + (f".{side}" if side else "")

    def _load(self, of: str, side: str = "") -> asyncssh.SSHKey | None:
        if self._vaulted(of):
            assert self.vault is not None
            id = self.vault_id(of, side)
            return asyncssh.import_private_key(self.vault.get(id)) if self.vault.has(id) else None
        path = self._file(of, side)
        return asyncssh.read_private_key(str(path)) if path.exists() else None

    def _store(self, key: asyncssh.SSHKey, of: str, side: str = "") -> None:
        if self._vaulted(of):
            assert self.vault is not None
            public = key.export_public_key().decode().strip()
            name = f"Kastellan's {of} key" + (f" ({side})" if side else "")
            self.vault.put(
                self.vault_id(of, side),
                key.export_private_key().decode(),
                kind="ssh-key",
                name=name,
                public=public,
            )
            return
        path = self._file(of, side)
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        temp = path.with_name(path.name + ".tmp")
        key.write_private_key(str(temp))
        os.chmod(temp, 0o600)
        if not side:
            key.write_public_key(str(path.with_suffix(".pub")))
        os.replace(temp, path)

    def _delete(self, of: str, side: str) -> None:
        if self._vaulted(of):
            assert self.vault is not None
            if self.vault.has(self.vault_id(of, side)):
                self.vault.delete(self.vault_id(of, side))
            return
        self._file(of, side).unlink(missing_ok=True)

    def take_in(self) -> tuple[list[str], list[Path]]:
        """Into the (new, unlocked) key ring: the keys kept as files that belong there, each read
        back from it. What was taken (vault ids) and the files to remove now (the caller does,
        once all is in)."""
        taken: list[str] = []
        gone: list[Path] = []
        for of in self.IN_VAULT:
            for side in ("", "next", "old"):
                path = self._file(of, side)
                if not path.exists():
                    continue
                key = asyncssh.read_private_key(str(path))
                self._store(key, of, side)
                back = self._load(of, side)
                if back is None or back.get_fingerprint() != key.get_fingerprint():
                    raise ValueError(f"the {of} key doesn't read back")
                taken.append(self.vault_id(of, side))
                gone += [path, path.with_suffix(".pub")] if not side else [path]
        return taken, gone

    def hub_key(self, which: str = "hub") -> asyncssh.SSHKey:
        """One of the hub's keys, made the first time it is needed (once, even when two requests
        ask at the same time; written whole, then renamed into place). vault.Locked while the
        key ring is locked."""
        with _make_lock:
            key = self._load(which)
            if key is None:
                key = asyncssh.generate_private_key("ssh-ed25519", comment=f"kastellan-{which}")
                self._store(key, which)
                log.info("made the hub's %s SSH key", which)
        return key

    def public_key(self, which: str = "hub") -> str:
        """The public half; also while the key ring is locked (setup commands show it)."""
        if self._vaulted(which):
            assert self.vault is not None
            entry = self.vault.entry(self.vault_id(which))
            if entry is not None and entry.public:
                return entry.public
        return self.hub_key(which).export_public_key().decode().strip()

    def known_public(self, of: str = "hub", side: str = "") -> asyncssh.SSHKey | None:
        """A key's public half if the key is there (also while the key ring is locked), else None."""
        if self._vaulted(of):
            assert self.vault is not None
            entry = self.vault.entry(self.vault_id(of, side))
            return asyncssh.import_public_key(entry.public) if entry is not None and entry.public else None
        path = self._file(of, side)
        pub = path.with_suffix(".pub")
        if not side and pub.exists():
            return asyncssh.read_public_key(str(pub))
        return asyncssh.read_private_key(str(path)).convert_to_public() if path.exists() else None

    def made(self, of: str = "hub") -> float | None:
        """When a key was made or last replaced (a time stamp), or None."""
        if self._vaulted(of):
            assert self.vault is not None
            entry = self.vault.entry(self.vault_id(of))
            return entry.changed or entry.created if entry is not None else None
        path = self._file(of, "")
        return path.stat().st_mtime if path.exists() else None

    # Rotating a key (`of`: the hub key in sshkeys.py, the VPN key in vpn/rotation.py): the next
    # key is made and added everywhere first, then it becomes the key and the old one stays until
    # it is removed everywhere. Meanwhile a login offers all of them, so a device on either key
    # keeps working.
    def side_key(self, which: str, of: str = "hub") -> asyncssh.SSHKey | None:
        """A key's "next" or "old" key, if a rotation has one."""
        return self._load(of, which)

    def make_next(self, of: str = "hub") -> asyncssh.SSHKey:
        with _make_lock:
            key = self._load(of, "next")
            if key is None:
                stamp = time.strftime("%Y-%m-%d")
                key = asyncssh.generate_private_key("ssh-ed25519", comment=f"kastellan-{of} {stamp}")
                self._store(key, of, "next")
                log.info("made the next %s SSH key", of)
        return key

    def promote_next(self, expect: str, of: str = "hub") -> None:
        """The next key (with fingerprint `expect`) becomes the key; the key so far is kept as
        "old". The key is never missing on the way (kept as old first, then replaced), and an
        old key that isn't the current key is never overwritten: it may be the only copy of a
        key that devices still have."""
        with _make_lock:
            nxt = self._load(of, "next")
            if nxt is None:
                raise FileNotFoundError("no next key")
            if nxt.get_fingerprint() != expect:
                raise ValueError("the next key isn't the one that was added")
            current = self._load(of)
            if current is None:
                raise FileNotFoundError("no current key")
            old = self._load(of, "old")
            if old is not None and old.get_fingerprint() != current.get_fingerprint():
                raise ValueError("an old key from an earlier rotation is still there; finish that first")
            self._store(current, of, "old")
            self._store(nxt, of)
            self._delete(of, "next")
            log.info("the next %s SSH key is now the key", of)

    def drop(self, which: str, of: str = "hub") -> None:
        """Forget the next key (cancelled) or the old one (removed everywhere)."""
        with _make_lock:
            self._delete(of, which)

    def _pinned(self) -> dict[str, list[asyncssh.SSHKey]]:
        out: dict[str, list[asyncssh.SSHKey]] = {}
        try:
            lines = self.known_hosts.read_text().splitlines()
        except OSError:
            return out
        for line in lines:
            parts = line.split()
            # Only plain lines as trust() writes them; hashed names (|1|) and @markers are not ours.
            if len(parts) < 3 or line.startswith(("#", "@", "|")):
                continue
            try:
                key = asyncssh.import_public_key(f"{parts[1]} {parts[2]}")
            except (ValueError, asyncssh.KeyImportError):
                continue
            for name in parts[0].split(","):
                out.setdefault(name, []).append(key)
        return out

    async def host_key(self, target: Target) -> asyncssh.SSHKey:
        """The key a host presents now (no login)."""
        key = await asyncio.wait_for(asyncssh.get_server_host_key(target.host, target.port), CONNECT_TIMEOUT)
        if key is None:
            raise OSError(f"{target.host} sent no host key")
        return key

    async def trust(self, target: Target, fingerprint: str) -> None:
        """Pin a host's key, if it still is the one the admin saw."""
        key = await self.host_key(target)
        if key.get_fingerprint() != fingerprint:
            raise ChangedHost(target.host, key.get_fingerprint())
        async with self._lock:
            pinned = self._pinned().get(target.name, [])
            if any(k.public_data == key.public_data for k in pinned):
                return
            if pinned:
                # A different key is already pinned: replacing it must be deliberate (`forget`).
                raise ChangedHost(target.host, key.get_fingerprint())
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.known_hosts.open("a") as f:
                f.write(f"{target.name} {key.export_public_key().decode().strip()}\n")
            os.chmod(self.known_hosts, 0o600)

    async def forget(self, target: Target) -> None:
        """Unpin a host (after a reinstall), so its new key can be trusted."""
        async with self._lock:
            try:
                lines = self.known_hosts.read_text().splitlines()
            except OSError:
                return
            keep = [ln for ln in lines if not ln.split() or target.name not in ln.split()[0].split(",")]
            self.known_hosts.write_text("".join(f"{ln}\n" for ln in keep))

    async def check_host(self, target: Target) -> str:
        """The fingerprint, if the host's key is pinned; UnknownHost / ChangedHost otherwise."""
        key = await self.host_key(target)
        pinned = self._pinned().get(target.name, [])
        if not pinned:
            raise UnknownHost(target.host, key.get_fingerprint())
        if not any(k.public_data == key.public_data for k in pinned):
            raise ChangedHost(target.host, key.get_fingerprint())
        return key.get_fingerprint()

    async def connect(
        self,
        target: Target,
        which: str = "hub",
        password: str | None = None,
        only: asyncssh.SSHKey | None = None,
    ) -> asyncssh.SSHClientConnection:
        """Log in with the hub's key, only to a host presenting a key pinned for this name.

        The pin is checked in the login connection itself: asyncssh gets exactly the keys pinned
        under `target.name` (not the file, whose lookup would also accept keys pinned under the
        resolved address or the plain host), and nothing else. A separate check before the login
        would be another connection, possibly to another machine (DNS)."""
        pinned = self._pinned().get(target.name, [])
        client = _Recorder()
        if password:
            keys: list[asyncssh.SSHKey] = []
        elif only is not None:
            keys = [only]  # e.g. to see that a new key works
        else:
            keys = [self.hub_key(which)]
            if which in ("hub", "vpn"):  # during a rotation, a device may be on the other key still
                keys += [k for k in (self.side_key("next", which), self.side_key("old", which)) if k]
        try:
            conn, _ = await asyncio.wait_for(
                asyncssh.create_connection(
                    lambda: client,
                    target.host,
                    target.port,
                    username=target.user,
                    # The hub's key; or, once, a password an admin typed to set up access.
                    client_keys=keys,
                    known_hosts=(pinned, [], []),
                    agent_path=None,  # only the hub's own key, never the agent of whoever runs it
                    password=password,
                    kbdint_auth=bool(password),  # PAM often asks for passwords that way
                    preferred_auth="keyboard-interactive,password" if password else "publickey",
                ),
                CONNECT_TIMEOUT,
            )
        except asyncssh.HostKeyNotVerifiable as e:
            fingerprint = client.seen.get_fingerprint() if client.seen else "unknown"
            raise (ChangedHost if pinned else UnknownHost)(target.host, fingerprint) from e
        except asyncssh.KeyExchangeFailed as e:
            if pinned:  # the host no longer offers the kind of key we pinned
                raise ChangedHost(target.host, "a different kind of key") from e
            raise
        return conn


class _Recorder(asyncssh.SSHClient):
    """Notes the host key a server presented when it was not a pinned one (for the message)."""

    def __init__(self) -> None:
        self.seen: asyncssh.SSHKey | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key: asyncssh.SSHKey) -> bool:
        self.seen = key
        return False
