"""Rotating Kastellan's VPN key (restricted mode), so a key that leaked or got old stops working.

The VPN key may only run kastellan-pki / kastellan-crl on its hosts, so it can't be added the way
the hub key is (a shell over the login). The scripts themselves take it: `add-key OLD NEW` copies
the line of OLD (same command=, from=, limits) with NEW in it, and `keep-key NEW` leaves NEW the
only key bound to that script (whatever else was added, e.g. by whoever held a leaked key, goes).

1. adding: the next key is made, added on the PKI host and the OpenVPN server with the current
   key, and a login with only the next key is tried;
2. switching: when it works on both (or the admin says to go on anyway), it becomes the key; the
   key so far is kept as "old" (crash-safe, as for the hub key);
3. removing: on each host, `keep-key` with the new key (a host skipped before gets the new key
   first, with the old one), and a login with only the old key must fail. A host that can't be
   reached holds this stage (and the old private key) until it works or an admin forgets it,
   knowing the old key may still be trusted there;
4. done: the old private key is deleted.
Logins with the VPN key offer every one of them meanwhile (ssh.py). The state is in
<data>/ssh/vpn-rotation.json. In direct mode the VPN key isn't used (the hub key is).
"""

from __future__ import annotations

import asyncio
import re

import asyncssh

from ..ssh import Keys, Target
from ..sshkeys import DeviceStep, Rotation, _now, _why


def _blob(key: asyncssh.SSHKey) -> str:
    """The base64 part of a public key: what the scripts take."""
    return key.export_public_key().decode().split()[1]


def _login(target: Target) -> str:
    return f"{target.user}@{target.host}:{target.port}"


def _moved(target: str | None) -> str:
    return f"not this host any more ({target}): the old key may still be trusted there; forget it to finish"


class VpnRotator:
    def __init__(self, keys: Keys) -> None:
        self.keys = keys
        self.path = keys.folder / "vpn-rotation.json"
        self._lock = asyncio.Lock()

    def state(self) -> Rotation | None:
        return Rotation.load(self.path)

    async def _run(self, target: Target, command: str, only: asyncssh.SSHKey) -> str:
        async with await self.keys.connect(target, "vpn", only=only) as conn:
            r = await conn.run(command, check=False, stdin=asyncssh.DEVNULL, timeout=30)
        if r.exit_status != 0:
            lines = str(r.stderr or "").strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"{command.split()[0]} failed")
        return str(r.stdout or "")

    async def _add(self, target: Target, key: asyncssh.SSHKey, using: asyncssh.SSHKey) -> None:
        """The key added with `using` (the key the host trusts now), and a login with it tried."""
        await self._run(target, f"add-key {_blob(using)} {_blob(key)}", only=using)
        async with await self.keys.connect(target, "vpn", only=key):
            pass

    async def _keep(self, target: Target, key: asyncssh.SSHKey) -> str:
        """Only `key` stays bound to the script; what it says it removed."""
        out = await self._run(target, f"keep-key {_blob(key)}", only=key)
        m = re.search(r"removed (\d+)", out)
        n = int(m.group(1)) if m else 0
        return f"removed {n} other key line{'s' if n != 1 else ''}"

    async def _step_add(self, target: Target, key: asyncssh.SSHKey) -> DeviceStep:
        step = DeviceStep(target=_login(target))
        try:
            await self._add(target, key, self.keys.hub_key("vpn"))
            step.added = True
        except Exception as e:  # one host's trouble is reported, never stops the other
            step.error = _why(e)
        return step

    async def _step_remove(self, target: Target, step: DeviceStep, old: asyncssh.SSHKey) -> DeviceStep:
        new = self.keys.hub_key("vpn")
        done = DeviceStep(added=step.added, target=_login(target))
        try:
            if not step.added:  # skipped at the switch: it gets the new key now, from the old
                await self._add(target, new, old)
                done.added = True
            done.note = await self._keep(target, new)
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
            steps = await asyncio.gather(*(self._step_add(t, key) for t in todo.values()))
            rot.devices.update(dict(zip(todo, steps, strict=True)))
            rot.save(self.path)
            return rot

    async def finish(self, hosts: dict[str, Target], anyway: bool = False) -> Rotation:
        """Make the next key the key, and leave it the only one on every host (again, for hosts
        that didn't work the last time)."""
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
                for r in missing:  # stays: it gets the new key with the old one when it answers
                    was = rot.devices.get(r)
                    why = was.error if was and was.error else "didn't get the new key"
                    rot.devices[r] = DeviceStep(
                        error=f"skipped at the switch: {why}", target=_login(hosts[r])
                    )
                rot.stage = "switching"
                rot.save(self.path)
            if rot.stage == "switching":
                if self.keys.hub_key("vpn").get_fingerprint() != rot.next:
                    self.keys.promote_next(expect=rot.next, of="vpn")
                rot.stage = "removing"
                rot.save(self.path)
            if rot.stage == "removing":
                old = self.keys.side_key("old", "vpn")
                todo = {}
                for r, s in rot.devices.items():
                    if s.removed or s.forgotten:
                        continue
                    if r not in hosts or s.target != _login(hosts[r]):
                        s.error = _moved(s.target)
                    elif old is None:
                        s.error = "the old private key is gone: remove the old key there by hand"
                    else:
                        todo[r] = s
                steps = await asyncio.gather(*(self._step_remove(hosts[r], s, old) for r, s in todo.items()))
                rot.devices.update(dict(zip(todo, steps, strict=True)))
                self._settle(rot)
            rot.save(self.path)
            return rot

    def _settle(self, rot: Rotation) -> None:
        """Done once every host is clean or given up on: then the old private key goes."""
        if all(s.removed or s.forgotten for s in rot.devices.values()):
            self.keys.drop("old", "vpn")
            rot.stage, rot.finished = "done", _now()

    async def forget(self, host: str) -> Rotation:
        """Give up on one host while removing: the old key may still be trusted there."""
        async with self._lock:
            rot = self.state()
            if rot is None or rot.stage != "removing" or host not in rot.devices:
                raise ValueError("nothing to forget")
            step = rot.devices[host]
            if not step.removed:
                step.forgotten = True
                step.error = (
                    f"forgotten: the old key may still be trusted on {step.target}; remove it there by hand"
                )
            self._settle(rot)
            rot.save(self.path)
            return rot

    async def cancel(self, hosts: dict[str, Target]) -> Rotation:
        """Before the switch: leave the current key the only one on every host of the rotation
        (so the next key goes, wherever it got to), forget the next key; per host what happened."""
        async with self._lock:
            rot = self.state()
            if rot is not None and rot.stage not in ("adding", "done"):
                raise ValueError("the switch happened already; finish removing the old key")
            out = Rotation(started=rot.started if rot else _now(), stage="cancelled")
            if rot is not None and rot.stage == "adding":
                current = self.keys.hub_key("vpn")

                async def one(r: str, s: DeviceStep) -> DeviceStep:
                    if r not in hosts or s.target != _login(hosts[r]):
                        return DeviceStep(target=s.target, error="not this host any more: not changed")
                    step = DeviceStep(target=s.target)
                    try:
                        step.note = await self._keep(hosts[r], current)
                    except Exception as e:  # without its private key the next key is harmless
                        step.error = _why(e)
                    return step

                names = list(rot.devices)
                steps = await asyncio.gather(*(one(r, rot.devices[r]) for r in names))
                out.devices = dict(zip(names, steps, strict=True))
            self.keys.drop("next", "vpn")
            self.path.unlink(missing_ok=True)
            return out
