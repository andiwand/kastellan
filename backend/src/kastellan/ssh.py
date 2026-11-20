"""SSH from the hub: its own key, pinned host keys, and connections to devices.

- The hub has one ed25519 key, made on first use: <data>/ssh/hub_ed25519 (0600) and .pub. Put
  the public key into the `authorized_keys` of the hosts Kastellan may log in to.
- Host keys are pinned in <data>/ssh/known_hosts. A host we have never seen is not trusted by
  itself: the connection fails with `UnknownHost` (with the fingerprint), and an admin confirms
  it in the app (`trust`). A host whose key changed fails with `ChangedHost`: that is either a
  reinstall or someone in between, and only a person can tell.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import asyncssh

log = logging.getLogger(__name__)

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


class Keys:
    # The hub's keys: "hub" for terminals and installs, "vpn" only for the restricted VPN scripts
    # (bound with command= where they are installed; separate so binding it takes nothing away).
    NAMES: ClassVar[dict[str, str]] = {"hub": "hub_ed25519", "vpn": "vpn_ed25519"}

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.private_path = folder / "hub_ed25519"
        self.known_hosts = folder / "known_hosts"
        self._lock = asyncio.Lock()

    def hub_key(self, which: str = "hub") -> asyncssh.SSHKey:
        """One of the hub's keys, made the first time it is needed."""
        path = self.folder / self.NAMES[which]
        if not path.exists():
            self.folder.mkdir(parents=True, exist_ok=True)
            os.chmod(self.folder, 0o700)
            comment = "kastellan-hub" if which == "hub" else f"kastellan-{which}"
            key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
            key.write_private_key(str(path))
            os.chmod(path, 0o600)
            key.write_public_key(str(path.with_suffix(".pub")))
            log.info("made the hub's SSH key %s", path)
        return asyncssh.read_private_key(str(path))

    def public_key(self, which: str = "hub") -> str:
        return self.hub_key(which).export_public_key().decode().strip()

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
        self, target: Target, which: str = "hub", password: str | None = None
    ) -> asyncssh.SSHClientConnection:
        """Log in with the hub's key, only to a host presenting a key pinned for this name.

        The pin is checked in the login connection itself: asyncssh gets exactly the keys pinned
        under `target.name` (not the file, whose lookup would also accept keys pinned under the
        resolved address or the plain host), and nothing else. A separate check before the login
        would be another connection, possibly to another machine (DNS)."""
        pinned = self._pinned().get(target.name, [])
        client = _Recorder()
        try:
            conn, _ = await asyncio.wait_for(
                asyncssh.create_connection(
                    lambda: client,
                    target.host,
                    target.port,
                    username=target.user,
                    # The hub's key; or, once, a password an admin typed to set up access.
                    client_keys=[] if password else [self.hub_key(which)],
                    known_hosts=(pinned, [], []),
                    agent_path=None,  # only the hub's own key, never the agent of whoever runs it
                    password=password,
                    kbdint_auth=False,
                    preferred_auth="password" if password else "publickey",
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
