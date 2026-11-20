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
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.private_path = folder / "hub_ed25519"
        self.known_hosts = folder / "known_hosts"
        self._lock = asyncio.Lock()

    def hub_key(self) -> asyncssh.SSHKey:
        """The hub's key, made the first time it is needed."""
        if not self.private_path.exists():
            self.folder.mkdir(parents=True, exist_ok=True)
            os.chmod(self.folder, 0o700)
            key = asyncssh.generate_private_key("ssh-ed25519", comment="kastellan-hub")
            key.write_private_key(str(self.private_path))
            os.chmod(self.private_path, 0o600)
            key.write_public_key(str(self.private_path.with_suffix(".pub")))
            log.info("made the hub's SSH key %s", self.private_path)
        return asyncssh.read_private_key(str(self.private_path))

    def public_key(self) -> str:
        return self.hub_key().export_public_key().decode().strip()

    def _pinned(self) -> dict[str, list[asyncssh.SSHKey]]:
        out: dict[str, list[asyncssh.SSHKey]] = {}
        try:
            lines = self.known_hosts.read_text().splitlines()
        except OSError:
            return out
        for line in lines:
            parts = line.split()
            if len(parts) < 3 or line.startswith("#"):
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
            keep = [ln for ln in lines if target.name not in ln.split()[0].split(",")] if lines else []
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

    async def connect(self, target: Target) -> asyncssh.SSHClientConnection:
        """Log in with the hub's key; only to a host whose key is pinned."""
        await self.check_host(target)
        return await asyncio.wait_for(
            asyncssh.connect(
                target.host,
                target.port,
                username=target.user,
                client_keys=[self.hub_key()],
                known_hosts=str(self.known_hosts),
                agent_path=None,  # only the hub's own key, never the agent of whoever runs it
                password=None,
                kbdint_auth=False,
            ),
            CONNECT_TIMEOUT,
        )
