"""SSH keys on the devices: which keys may log in where, removing one, and rotating the hub's
own key.

Reading: over the device's `ssh:` login. As root, or with sudo without a password, every user's
`authorized_keys` (each read *as that user*, with `su`, so a link in someone's home can't make
root print another file); otherwise the login's own. On OpenWrt, root's keys are in
/etc/dropbear/authorized_keys. The device's answer is untrusted: rows split on newlines only,
users and files must be ones the listing script can produce, key lines are parsed the way sshd
reads them (options with quotes first), and anything else is dropped.

Removing: the lines to remove go on stdin (never as arguments), the file is rebuilt next to
itself and moved into place (as its user), and the hub lists again to see the key is gone.

Rotating the hub key, so a key that leaked or got old stops working everywhere:
1. adding: a next key is made and added to every device with `ssh:` (with the same `from=` as
   setup), and a login with only the next key is tried;
2. switching: when it works everywhere (or the admin says to go on anyway), the next key
   becomes the key; the key so far is kept as "old" (crash-safe: the stage is saved first, and
   the switch can be resumed);
3. removing: on each device, a login with only the new key must work; then the old key is
   removed, and a login with only the old key must fail;
4. done: the old private key is deleted.
Meanwhile logins offer every key (ssh.py), so nothing breaks half-way. The state is in
<data>/ssh/rotation.json. One device is changed by one thing at a time (`device_lock`).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncssh

from .access import USER, authorize, hub_address, openssh_options
from .config import Device
from .ssh import Keys, Target

KEY_TYPE = re.compile(
    r"^(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-nistp\d+|sk-[\w@.-]+)(?:-cert-v01@openssh\.com)?$"
)
BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,3}$")
FILE = re.compile(r"^/(?:[^\0\n]*/)?\.ssh/authorized_keys2?$")
DROPBEAR = "/etc/dropbear/authorized_keys"

# Prints "user<TAB>file<TAB>line" for every line of every authorized_keys it may read, and
# "!partial" when it can't read other users' (no su).
_LIST = r"""
emit() { while IFS= read -r l || [ -n "$l" ]; do printf '%s\t%s\t%s\n' "$1" "$2" "$l"; done; }
me=$(id -un)
if [ "$(id -u)" = 0 ]; then
  if [ -f /etc/openwrt_release ] && [ -f /etc/dropbear/authorized_keys ]; then
    emit root /etc/dropbear/authorized_keys < /etc/dropbear/authorized_keys
  fi
  command -v su >/dev/null || echo '!partial'
  (getent passwd 2>/dev/null || cat /etc/passwd) | while IFS=: read -r u _ _ _ _ home _; do
    [ -n "$home" ] && [ -d "$home/.ssh" ] || continue
    for f in "$home/.ssh/authorized_keys" "$home/.ssh/authorized_keys2"; do
      [ -e "$f" ] || continue
      if [ "$u" = root ]; then emit "$u" "$f" < "$f"
      elif command -v su >/dev/null; then
        su -s /bin/sh "$u" -c 'cat "$0"' "$f" 2>/dev/null | emit "$u" "$f"
      fi
    done
  done
else
  for f in "$HOME/.ssh/authorized_keys" "$HOME/.ssh/authorized_keys2"; do
    [ -r "$f" ] && emit "$me" "$f" < "$f"
  done
fi
"""

# Run as the file's user, $0 the file; the lines to remove come on stdin. Every line that isn't
# one of them (compared without a trailing CR) stays; the result is built next to the file and
# moved into place, so a full disk or a dropped session never leaves half a file.
_REMOVE = r"""set -eu
f=$0
[ -f "$f" ] || { echo "no file $f" >&2; exit 1; }
d=$(dirname "$f")
drop=$(mktemp "$d/.kastellan-drop.XXXXXX")
new=$(mktemp "$d/.kastellan-new.XXXXXX")
trap 'rm -f "$drop" "$new"' EXIT
cat > "$drop"
while IFS= read -r l || [ -n "$l" ]; do
  c=$(printf '%s' "$l" | tr -d '\r')
  if [ -n "$c" ] && grep -qxF -- "$c" "$drop"; then continue; fi
  printf '%s\n' "$l" >> "$new"
done < "$f"
chmod 600 "$new"
mv -f "$new" "$f"
"""

MAX_OUTPUT = 512 * 1024

_locks: dict[str, asyncio.Lock] = {}


def device_lock(id: str) -> asyncio.Lock:
    """One change to a device's keys at a time (a removal and a rotation would race)."""
    return _locks.setdefault(id, asyncio.Lock())


@dataclass(frozen=True)
class AuthorizedKey:
    user: str
    file: str
    line: str  # as it is in the file (without a trailing CR)
    type: str
    fingerprint: str
    comment: str
    options: str

    def view(self, labels: dict[str, str]) -> dict[str, Any]:
        return {
            "user": self.user,
            "file": self.file,
            "type": self.type,
            "fingerprint": self.fingerprint,
            "comment": self.comment[:200],
            "options": self.options[:300],
            "kastellan": labels.get(self.fingerprint),
        }


def _options_end(text: str) -> int:
    """Where sshd's options end: the first whitespace outside double quotes (with \\")."""
    quoted = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and quoted and i + 1 < len(text):
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif ch in " \t" and not quoted:
            return i
        i += 1
    return -1


def parse_line(text: str) -> tuple[str, str, str, str] | None:
    """(options, type, base64, comment) of a key line, as sshd reads it, or None."""
    text = text.strip()
    if not text or text.startswith("#"):
        return None
    options = ""
    first = re.split(r"[ \t]+", text, maxsplit=1)[0]
    if not KEY_TYPE.match(first):
        end = _options_end(text)
        if end < 0:
            return None
        options, text = text[:end], text[end:].lstrip(" \t")
    parts = re.split(r"[ \t]+", text, maxsplit=2)
    if len(parts) < 2 or not KEY_TYPE.match(parts[0]) or not BASE64.match(parts[1]):
        return None
    return options, parts[0], parts[1], parts[2] if len(parts) > 2 else ""


def parse(output: str) -> tuple[list[AuthorizedKey], bool]:
    """The keys in the listing, and whether it was partial. Rows only on "\\n"; a user or file
    the listing script can't produce is someone's trick, and dropped."""
    out = []
    partial = False
    for row in output.split("\n"):
        if row == "!partial":
            partial = True
            continue
        parts = row.split("\t", 2)
        if len(parts) != 3:
            continue
        user, file, line = parts
        line = line.rstrip("\r")
        if not USER.match(user) or not (FILE.match(file) or (file == DROPBEAR and user == "root")):
            continue
        parsed = parse_line(line)
        if parsed is None:
            continue
        options, kind, data, comment = parsed
        try:
            fingerprint = asyncssh.import_public_key(f"{kind} {data}").get_fingerprint()
        except (ValueError, asyncssh.KeyImportError):
            continue
        out.append(AuthorizedKey(user, file, line, kind, fingerprint, comment.strip(), options))
    return out, partial


def kastellan_keys(keys: Keys) -> dict[str, str]:
    """Kastellan's own keys by fingerprint, named for the page (never removed by hand)."""
    out = {keys.hub_key().get_fingerprint(): "Kastellan"}
    for which, name in (("next", "Kastellan (new key)"), ("old", "Kastellan (old key)")):
        key = keys.side_key(which)
        if key is not None:
            out[key.get_fingerprint()] = name
    named = {"vpn": "Kastellan VPN", "collect": "Kastellan collector", "act": "Kastellan actor"}
    for which, name in named.items():
        if (keys.folder / keys.NAMES[which]).exists():
            out[keys.hub_key(which).get_fingerprint()] = name
    for side in ("next", "old"):  # a VPN key rotation's other key
        if key := keys.side_key(side, "vpn"):
            out[key.get_fingerprint()] = f"Kastellan VPN ({side} key)"
    return out


def target_of(device: Device) -> Target:
    if device.ssh is None:
        raise ValueError("the device has no ssh: login in config.yaml")
    return Target(device.host, device.ssh.port, device.ssh.user)


async def _privileged(conn: asyncssh.SSHClientConnection) -> str | None:
    """How to run something with root's rights here: "" (the login is root), "sudo -n ", or None
    (it can't)."""
    r = await conn.run(
        "id -u; sudo -n true 2>/dev/null && echo sudo", check=False, stdin=asyncssh.DEVNULL, timeout=20
    )
    out = str(r.stdout or "").split()
    if out[:1] == ["0"]:
        return ""
    return "sudo -n " if "sudo" in out else None


async def list_keys(keys: Keys, device: Device) -> tuple[list[AuthorizedKey], bool]:
    """The keys allowed on a device, and whether that is everyone's (False: only the login's)."""
    async with await keys.connect(target_of(device)) as conn:
        how = await _privileged(conn)
        prefix = how if how is not None else ""
        # Bytes: one stray byte in someone's file must not break the listing.
        r = await conn.run(f"{prefix}sh -s", input=_LIST.encode(), check=False, timeout=60, encoding=None)
    raw = r.stdout if isinstance(r.stdout, bytes) else b""
    found, partial = parse(raw[:MAX_OUTPUT].decode("utf-8", errors="replace"))
    return found, how is not None and not partial


async def remove_lines(keys: Keys, device: Device, user: str, file: str, lines: list[str]) -> None:
    """Remove exact lines from one authorized_keys, written as its user."""
    if not lines:
        return
    q = shlex.quote
    me = target_of(device).user
    async with await keys.connect(target_of(device)) as conn:
        how = await _privileged(conn)
        if user == me:
            command = f"sh -c {q(_REMOVE)} {q(file)}"
        elif how is None:
            raise PermissionError(f"{me} may only change its own keys here (it isn't root and has no sudo)")
        elif user == "root":
            command = f"{how}sh -c {q(_REMOVE)} {q(file)}"
        else:  # as that user: a link in their home can't send root's write anywhere
            command = f"{how}su -s /bin/sh {q(user)} -c {q(_REMOVE)} {q(file)}"
        r = await conn.run(command, input="".join(f"{ln}\n" for ln in lines), check=False, timeout=30)
    if r.exit_status != 0:
        message = str(r.stderr or "").strip().splitlines()
        raise RuntimeError(message[-1] if message else "removing the key failed")


async def remove_key(keys: Keys, device: Device, user: str, file: str, fingerprint: str) -> int:
    """Remove a key (every line of it in that file), then check it is gone. Returns how many
    lines were removed."""
    found, _ = await list_keys(keys, device)
    lines = [k.line for k in found if (k.user, k.file, k.fingerprint) == (user, file, fingerprint)]
    if not lines:
        raise LookupError("that key isn't there (any more)")
    await remove_lines(keys, device, user, file, lines)
    after, _ = await list_keys(keys, device)
    if any((k.user, k.file, k.fingerprint) == (user, file, fingerprint) for k in after):
        raise RuntimeError("the key is still there after removing it")
    return len(lines)


# --- Rotating the hub key -----------------------------------------------------------------------


@dataclass
class DeviceStep:
    added: bool = False  # the next key is in its authorized_keys and works
    removed: bool = False  # the old key is gone from it, and no longer works
    error: str | None = None
    target: str | None = None  # the login it was done for (user@host:port)
    note: str | None = None  # what the step found (e.g. other keys it removed)
    forgotten: bool = False  # given up on by an admin: the old key may still work there


@dataclass
class Rotation:
    started: str
    next: str = ""  # the next key's fingerprint
    stage: str = "adding"  # adding, switching, removing, done
    devices: dict[str, DeviceStep] = field(default_factory=dict)
    finished: str | None = None

    @classmethod
    def load(cls, path: Path) -> Rotation | None:
        try:
            data = json.loads(path.read_text())
            data["devices"] = {k: DeviceStep(**v) for k, v in data.get("devices", {}).items()}
            return cls(**data)
        except (OSError, ValueError, TypeError):
            return None

    def save(self, path: Path) -> None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), indent=1))
        os.chmod(temp, 0o600)
        os.replace(temp, path)


def login_of(device: Device) -> str:
    """The login a rotation step was done for: user@host:port."""
    t = target_of(device)
    return f"{t.user}@{t.host}:{t.port}"


def _why(e: Exception) -> str:
    return str(e)[:200] or type(e).__name__


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Rotator:
    def __init__(self, keys: Keys) -> None:
        self.keys = keys
        self.path = keys.folder / "rotation.json"
        self._lock = asyncio.Lock()

    def state(self) -> Rotation | None:
        return Rotation.load(self.path)

    async def _add(self, id: str, device: Device, key: asyncssh.SSHKey) -> DeviceStep:
        """Put the next key on a device (as setup does), then log in with only that key."""
        step = DeviceStep(target=login_of(device))
        try:
            target = target_of(device)
            public = key.export_public_key().decode().strip()
            hub_ip = await asyncio.to_thread(hub_address, device.host, target.port)
            script = "\n".join(["set -eu", *authorize(target.user, openssh_options(hub_ip) + public, public)])
            async with device_lock(id):
                async with await self.keys.connect(target) as conn:
                    r = await conn.run("sh -s", input=script + "\n", check=False, timeout=30)
                if r.exit_status != 0:
                    lines = str(r.stderr or "").strip().splitlines()
                    raise RuntimeError(lines[-1] if lines else "adding the key failed")
                async with await self.keys.connect(target, only=key) as conn:
                    await conn.run("true", check=False, stdin=asyncssh.DEVNULL, timeout=20)
            step.added = True
        except Exception as e:  # one device's trouble is reported, never stops the others
            step.error = _why(e)
        return step

    async def _remove_old(
        self, id: str, device: Device, old: asyncssh.SSHKey, step: DeviceStep
    ) -> DeviceStep:
        """On one device: the new key must work; then the old one goes, and must stop working."""
        done = DeviceStep(added=True, target=step.target)
        try:
            target = target_of(device)
            async with device_lock(id):
                async with await self.keys.connect(target, only=self.keys.hub_key()) as conn:
                    await conn.run("true", check=False, stdin=asyncssh.DEVNULL, timeout=20)
                fingerprint = old.get_fingerprint()
                found, _ = await list_keys(self.keys, device)
                mine = [k for k in found if k.fingerprint == fingerprint and k.user == target.user]
                files = {k.file for k in mine}
                for file in files:
                    await remove_lines(
                        self.keys, device, target.user, file, [k.line for k in mine if k.file == file]
                    )
                try:
                    async with await self.keys.connect(target, only=old):
                        pass
                    raise RuntimeError("the old key still logs in (in a file Kastellan can't change?)")
                except asyncssh.PermissionDenied:
                    pass  # as it should be
            done.removed = True
        except Exception as e:
            done.error = _why(e)
        return done

    async def start(self, devices: dict[str, Device]) -> Rotation:
        """Make the next key (or reuse it) and add it everywhere it isn't yet."""
        async with self._lock:
            rot = self.state()
            if rot is None or rot.stage == "done":
                rot = Rotation(started=_now())
            if rot.stage != "adding":
                raise ValueError("the switch happened already; finish removing the old key")
            key = self.keys.make_next()
            if rot.next != key.get_fingerprint():  # another next key: what was added was another
                rot.next, rot.devices = key.get_fingerprint(), {}
            # Devices that lost their ssh: since don't hold it up; a changed login starts over.
            rot.devices = {
                id: s
                for id, s in rot.devices.items()
                if id in devices and devices[id].ssh and s.target == login_of(devices[id])
            }
            todo = {
                id: d for id, d in devices.items() if d.ssh and not rot.devices.get(id, DeviceStep()).added
            }
            steps = await asyncio.gather(*(self._add(id, d, key) for id, d in todo.items()))
            rot.devices.update(dict(zip(todo, steps, strict=True)))
            rot.save(self.path)
            return rot

    def _switch(self, rot: Rotation) -> None:
        """Make the next key the key, once; resumable after a crash."""
        rot.stage = "switching"
        rot.save(self.path)
        current = self.keys.hub_key().get_fingerprint()
        if current != rot.next:
            self.keys.promote_next(expect=rot.next)
        rot.stage = "removing"
        rot.save(self.path)

    async def finish(self, devices: dict[str, Device], anyway: bool = False) -> Rotation:
        """Make the next key the key, and remove the old one from every device."""
        async with self._lock:
            rot = self.state()
            if rot is None:
                raise ValueError("no rotation started")
            if rot.stage == "adding":
                ready = {
                    id
                    for id, s in rot.devices.items()
                    if s.added and id in devices and devices[id].ssh and s.target == login_of(devices[id])
                }
                missing = [id for id, d in devices.items() if d.ssh and id not in ready]
                if missing and not anyway:
                    raise ValueError(f"the new key isn't on {', '.join(sorted(missing))} yet")
                rot.devices = {id: s for id, s in rot.devices.items() if id in ready}
            if rot.stage in ("adding", "switching"):
                self._switch(rot)
            old = self.keys.side_key("old")
            # Only devices still with this login; one that left (or changed its login) is noted.
            for id, s in list(rot.devices.items()):
                if id not in devices or not devices[id].ssh or s.target != login_of(devices[id]):
                    rot.devices[id] = DeviceStep(
                        added=s.added,
                        removed=True,
                        target=s.target,
                        error="not in config.yaml any more: not checked",
                    )
            if old is not None:
                todo = {id: s for id, s in rot.devices.items() if not s.removed}
                steps = await asyncio.gather(
                    *(self._remove_old(id, devices[id], old, s) for id, s in todo.items())
                )
                rot.devices.update(dict(zip(todo, steps, strict=True)))
            if all(s.removed for s in rot.devices.values()):
                self.keys.drop("old")
                rot.stage, rot.finished = "done", _now()
            rot.save(self.path)
            return rot

    async def cancel(self) -> None:
        """Before the switch: forget the next key (where it was added it stays harmless until
        removed by hand: its private key is gone)."""
        async with self._lock:
            rot = self.state()
            if rot is not None and rot.stage not in ("adding", "done"):
                raise ValueError("the switch happened already; finish removing the old key")
            self.keys.drop("next")
            self.path.unlink(missing_ok=True)
