"""Rotating Kastellan's VPN key (restricted mode), so a key that leaked or got old stops working.

The VPN key may only run kastellan-pki / kastellan-crl on its hosts, so it can't be added the way
the hub key is (a shell over the login). The scripts themselves take it: `add-key OLD NEW` copies
the line of OLD (same command=, from=, limits) with NEW in it, and `remove-key OLD` drops it
(never the last key for that script). So the key can only ever hand on exactly its own power.

1. adding: the next key is made, added on the PKI host and the OpenVPN server with the current
   key, and a login with only the next key is tried;
2. switching: when it works on both (or the admin says to go on anyway), it becomes the key; the
   key so far is kept as "old" (crash-safe, as for the hub key);
3. removing: on each host, the old key is removed with the new key, and a login with only the
   old key must fail;
4. done: the old private key is deleted.
Logins with the VPN key offer every one of them meanwhile (ssh.py). The state is in
<data>/ssh/vpn-rotation.json. In direct mode the VPN key isn't used (the hub key is).
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncssh

from ..ssh import Keys, Target
from ..sshkeys import DeviceStep, Rotation, _now, _why


def _blob(key: asyncssh.SSHKey) -> str:
    """The base64 part of a public key: what the scripts take."""
    return key.export_public_key().decode().split()[1]


def _login(target: Target) -> str:
    return f"{target.user}@{target.host}:{target.port}"


class VpnRotator:
    def __init__(self, keys: Keys) -> None:
        self.keys = keys
        self.path = keys.folder / "vpn-rotation.json"
        self._lock = asyncio.Lock()

    def state(self) -> Rotation | None:
        return Rotation.load(self.path)

    async def _run(self, target: Target, command: str, only: asyncssh.SSHKey | None = None) -> None:
        async with await self.keys.connect(target, "vpn", only=only) as conn:
            r = await conn.run(command, check=False, stdin=asyncssh.DEVNULL, timeout=30)
        if r.exit_status != 0:
            lines = str(r.stderr or "").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"{command.split()[0]} failed")

    async def _add(self, target: Target, key: asyncssh.SSHKey) -> DeviceStep:
        step = DeviceStep(target=_login(target))
        try:
            await self._run(target, f"add-key {_blob(self.keys.hub_key('vpn'))} {_blob(key)}")
            async with await self.keys.connect(target, "vpn", only=key):
                pass  # it logs in with only the next key
            step.added = True
        except Exception as e:  # one host's trouble is reported, never stops the other
            step.error = _why(e)
        return step

    async def _remove_old(self, target: Target, old: asyncssh.SSHKey) -> DeviceStep:
        done = DeviceStep(added=True, target=_login(target))
        try:
            await self._run(target, f"remove-key {_blob(old)}", only=self.keys.hub_key("vpn"))
            try:
                async with await self.keys.connect(target, "vpn", only=old):
                    pass
                raise RuntimeError("the old key still logs in (a line Kastellan's script can't change?)")
            except asyncssh.PermissionDenied:
                pass  # as it should be
            done.removed = True
        except Exception as e:
            done.error = _why(e)
        return done

    async def start(self, hosts: dict[str, Target]) -> Rotation:
        """Make the next key (or reuse it) and add it on every host that hasn't it yet."""
        async with self._lock:
            rot = self.state()
            if rot is None or rot.stage == "done":
                rot = Rotation(started=_now())
            if rot.stage != "adding":
                raise ValueError("the switch happened already; finish removing the old key")
            key = self.keys.make_next("vpn")
            if rot.next != key.get_fingerprint():
                rot.next, rot.devices = key.get_fingerprint(), {}
            rot.devices = {
                r: s for r, s in rot.devices.items() if r in hosts and s.target == _login(hosts[r])
            }
            todo = {r: t for r, t in hosts.items() if not rot.devices.get(r, DeviceStep()).added}
            steps = await asyncio.gather(*(self._add(t, key) for t in todo.values()))
            rot.devices.update(dict(zip(todo, steps, strict=True)))
            rot.save(self.path)
            return rot

    async def finish(self, hosts: dict[str, Target], anyway: bool = False) -> Rotation:
        """Make the next key the key, and remove the old one from every host."""
        async with self._lock:
            rot = self.state()
            if rot is None:
                raise ValueError("no rotation started")
            if rot.stage == "adding":
                ready = {
                    r
                    for r, s in rot.devices.items()
                    if s.added and r in hosts and s.target == _login(hosts[r])
                }
                missing = sorted(set(hosts) - ready)
                if missing and not anyway:
                    raise ValueError(f"the new key isn't on {', '.join(missing)} yet")
                rot.devices = {r: s for r, s in rot.devices.items() if r in ready}
                rot.stage = "switching"
                rot.save(self.path)
            if rot.stage == "switching":
                if self.keys.hub_key("vpn").get_fingerprint() != rot.next:
                    self.keys.promote_next(expect=rot.next, of="vpn")
                rot.stage = "removing"
                rot.save(self.path)
            old = self.keys.side_key("old", "vpn")
            for r, s in list(rot.devices.items()):
                if r not in hosts or s.target != _login(hosts[r]):
                    rot.devices[r] = DeviceStep(True, True, "not this host any more: not checked", s.target)
            if old is not None:
                todo = {r: hosts[r] for r, s in rot.devices.items() if not s.removed}
                steps = await asyncio.gather(*(self._remove_old(t, old) for t in todo.values()))
                rot.devices.update(dict(zip(todo, steps, strict=True)))
            if all(s.removed for s in rot.devices.values()):
                self.keys.drop("old", "vpn")
                rot.stage, rot.finished = "done", _now()
            rot.save(self.path)
            return rot

    async def cancel(self, hosts: dict[str, Target]) -> None:
        """Before the switch: take the next key off the hosts it reached (best effort; without its
        private key it is harmless anyway), and forget it."""
        async with self._lock:
            rot = self.state()
            if rot is not None and rot.stage not in ("adding", "done"):
                raise ValueError("the switch happened already; finish removing the old key")
            nxt = self.keys.side_key("next", "vpn")
            if rot is not None and nxt is not None:
                current = self.keys.hub_key("vpn")
                for r, s in rot.devices.items():
                    if s.added and r in hosts:
                        with contextlib.suppress(Exception):
                            await self._run(hosts[r], f"remove-key {_blob(nxt)}", only=current)
            self.keys.drop("next", "vpn")
            self.path.unlink(missing_ok=True)
