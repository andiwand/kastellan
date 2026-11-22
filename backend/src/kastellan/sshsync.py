"""SSH files kept alike across a group's devices: fetch, edit, push.

Fetch reads one file (`authorized_keys` or `known_hosts`) of one user on every member with
`ssh:`, as that user (the login's own; another user's with `su`, as sshkeys.py does, so a link in
their home can't make root read or write anywhere else), and lines the entries up: a key (by type
and data) or a known host (by its host field and key type), and on which devices it is, the same
or different. Each device's file gets a version (a hash of it as read).

Push changes exactly what was asked, device by device: entries removed, lines added where they
are missing; every other line, comment and the order stay. A device whose file changed since it
was fetched is refused. The file is written next to itself and moved into place, as its user;
then read back. Kastellan's own keys can't be removed here (rotate them instead), so the hub
never locks itself out.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncssh

from .access import DROPBEAR, valid_user
from .config import Device
from .ssh import Keys
from .sshkeys import KEY_TYPE, MAX_OUTPUT, parse_line, privileged, target_of

File = Literal["authorized_keys", "known_hosts"]
FILES = ("authorized_keys", "known_hosts")

# $0: the file's name. The file of the user it runs as (OpenWrt's root with dropbear: dropbear's
# for keys).
_WHERE = (
    r"""me=$(id -un)
if [ "$0" = authorized_keys ] && [ "$me" = root ] && """
    + DROPBEAR
    + r"""; then
  f=/etc/dropbear/authorized_keys
else
  home=$(getent passwd "$me" 2>/dev/null | cut -d: -f6)
  [ -n "$home" ] || home=$(awk -F: -v u="$me" '$1 == u { print $6 }' /etc/passwd)
  [ -n "$home" ] || { echo "no home folder for $me" >&2; exit 1; }
  f="$home/.ssh/$0"
fi
"""
)

# Prints the path and whether it exists, then the file.
_READ = (
    _WHERE
    + r"""if [ -f "$f" ]; then printf '%s\tyes\n' "$f"; cat "$f"; else printf '%s\tno\n' "$f"; fi
"""
)

# The new file on stdin: built next to the old one and moved into place (a full disk or a
# dropped session never leaves half a file); ~/.ssh made (700) if there is none. $1: the version
# it was fetched at, checked again right before the move (something else may have changed it
# meanwhile: a key rotation, a person). A link is followed: what it points to is changed.
_WRITE = (
    "set -eu\n"
    + _WHERE
    + r"""if [ -L "$f" ]; then f=$(readlink -f "$f"); fi
now() { if [ -e "$f" ]; then sha256sum < "$f" | cut -c1-16; else echo none; fi; }
d=$(dirname "$f")
[ -d "$d" ] || { mkdir -p "$d"; chmod 700 "$d"; }
new=$(mktemp "$d/.kastellan-new.XXXXXX")
trap 'rm -f "$new"' EXIT
cat > "$new"
chmod 600 "$new"
if command -v sha256sum >/dev/null && [ "$(now)" != "$1" ]; then
  echo "changed since it was fetched: fetch again" >&2; exit 1
fi
mv -f "$new" "$f"
trap - EXIT
"""
)

HOSTS = re.compile(r"^[A-Za-z0-9.*?:\[\]!,_|=+/-]{1,1000}$")
MARKER = ("@cert-authority", "@revoked")
BASE64 = re.compile(r"^[A-Za-z0-9+/]+={0,3}$")


@dataclass
class Entry:
    """One line of the file that means something: its identity (`key`), and how it shows."""

    key: str  # authorized_keys: "type data"; known_hosts: "[marker ]hosts type"
    line: str
    type: str
    fingerprint: str
    label: str  # authorized_keys: the comment; known_hosts: the hosts (hashed: "hashed")
    variant: str  # what may differ between devices: options + comment, or the host key


@dataclass
class Copy:
    """A device's file as fetched."""

    path: str = ""
    exists: bool = False
    raw: bytes = b""
    version: str = ""
    entries: list[Entry] = field(default_factory=list)


def fingerprint_of(kind: str, data: str) -> str | None:
    """A public key's fingerprint, or None if it isn't one."""
    try:
        return asyncssh.import_public_key(f"{kind} {data}").get_fingerprint()
    except (ValueError, asyncssh.KeyImportError):
        return None


def parse_entry(file: File, line: str) -> Entry | None:
    """A line as sshd (authorized_keys) or ssh (known_hosts) reads it; None for comments, blank
    lines and anything it can't read."""
    text = line.rstrip("\r")
    if file == "authorized_keys":
        parsed = parse_line(text)
        if parsed is None:
            return None
        options, kind, data, comment = parsed
        fp = fingerprint_of(kind, data)
        if fp is None:
            return None
        return Entry(f"{kind} {data}", text, kind, fp, comment.strip(), f"{options} {comment}".strip())
    words = text.split()
    if not words or words[0].startswith("#"):
        return None
    marker = words.pop(0) if words[0] in MARKER else ""
    if len(words) < 3 or not HOSTS.match(words[0]) or not KEY_TYPE.match(words[1]):
        return None
    if not BASE64.match(words[2]):
        return None
    fp = fingerprint_of(words[1], words[2])
    if fp is None:
        return None
    hosts = words[0]
    label = "hashed" if hosts.startswith("|1|") else hosts
    key = f"{marker} {hosts} {words[1]}".strip()
    return Entry(key, text, words[1], fp, f"{marker} {label}".strip(), words[2])


def version_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def _lines(raw: bytes) -> list[str]:
    # surrogateescape: any byte comes back as it was when the file is written again.
    text = raw.decode("utf-8", errors="surrogateescape")
    return text.split("\n")[:-1] if text.endswith("\n") else text.split("\n") if text else []


def read_output(file: File, out: bytes) -> Copy:
    head, _, body = out.partition(b"\n")
    path, _, exists = head.decode(errors="replace").partition("\t")
    copy = Copy(path=path[:512], exists=exists == "yes", raw=body if exists == "yes" else b"")
    copy.version = version_of(copy.raw) if copy.exists else "none"
    copy.entries = [e for ln in _lines(copy.raw) if (e := parse_entry(file, ln))]
    return copy


def check_line(file: File, line: str) -> Entry:
    """A line to add, checked strictly: one line that reads as an entry, nothing else."""
    if "\n" in line or "\r" in line or len(line) > 8192:
        raise ValueError("one line, please")
    entry = parse_entry(file, line.strip())
    if entry is None:
        what = "a key line (options, type, key, comment)" if file == "authorized_keys" else "hosts, type, key"
        raise ValueError(f"not {what}: {line[:60]!r}")
    return entry


def apply(file: File, raw: bytes, add: list[str], remove: set[str]) -> tuple[bytes, list[Entry], list[Entry]]:
    """The file with the `remove` entries gone and the `add` lines in: appended where missing,
    or in place of the line with the same key (other options, another host key: removing and
    adding a key back is changing it, never losing it). Every other line as it was. Returns
    (new file, added, removed)."""
    kept, removed = [], []
    for ln in _lines(raw):
        e = parse_entry(file, ln)
        if e is not None and e.key in remove:
            removed.append(e)
        else:
            kept.append(ln)
    where = {e.key: i for i, ln in enumerate(kept) if (e := parse_entry(file, ln))}
    added = []
    for line in add:
        e = check_line(file, line)
        if e.key not in where:
            where[e.key] = len(kept)
            kept.append(e.line)
            added.append(e)
        elif kept[where[e.key]].rstrip("\r") != e.line:
            old = parse_entry(file, kept[where[e.key]])
            if old is not None:
                removed.append(old)
            kept[where[e.key]] = e.line
            added.append(e)
    if not added and not removed:
        return raw, [], []
    return ("\n".join(kept) + "\n").encode("utf-8", errors="surrogateescape"), added, removed


LOGIN = "@login"  # in a list of users: each device's own ssh: login


def _command(script: str, file: File, user: str, me: str, how: str | None) -> str:
    q = shlex.quote
    if user == me:
        return f"sh -c {q(script)} {file}"
    if how is None:
        raise PermissionError(f"{me} may only use its own files here (it isn't root and has no sudo)")
    if user == "root":
        return f"{how}sh -c {q(script)} {file}"
    return f"{how}su -s /bin/sh {q(user)} -c {q(script)} {file}"


def users_of(device: Device, users: list[str]) -> list[str]:
    """The users asked for on this device (`@login`: its login), each once."""
    me = target_of(device).user
    out: list[str] = []
    for u in users or [LOGIN]:
        name = me if u == LOGIN else u
        if not valid_user(name):
            raise ValueError(f"user names are letters, digits, . _ - (up to 32): {u[:40]!r}")
        if name not in out:
            out.append(name)
    return out


async def _read(conn: asyncssh.SSHClientConnection, file: File, user: str, me: str, how: str | None) -> Copy:
    if user != me:
        r = await conn.run(f"id -u {shlex.quote(user)}", check=False, timeout=20, stdin=asyncssh.DEVNULL)
        if r.exit_status != 0:
            raise LookupError("no such user")
    r = await conn.run(
        _command(_READ, file, user, me, how), check=False, timeout=30, encoding=None, stdin=asyncssh.DEVNULL
    )
    if r.exit_status != 0:
        err = (r.stderr or b"").decode(errors="replace").strip().splitlines()
        raise RuntimeError(err[-1][:200] if err else f"can't read it (exit {r.exit_status})")
    out = r.stdout if isinstance(r.stdout, bytes) else b""
    if len(out) > MAX_OUTPUT:
        raise RuntimeError("the file is too large to handle here")
    return read_output(file, out)


async def fetch(keys: Keys, device: Device, file: File, users: list[str]) -> dict[str, Copy | Exception]:
    """Each user's file on one device (one login), or why it can't be read."""
    me = target_of(device).user
    names = users_of(device, users)
    out: dict[str, Copy | Exception] = {}
    async with await keys.connect(target_of(device)) as conn:
        how = await privileged(conn) if any(n != me for n in names) else None
        for name in names:
            try:
                out[name] = await _read(conn, file, name, me, how)
            except (LookupError, PermissionError, RuntimeError) as e:
                out[name] = e
    return out


async def push(
    keys: Keys,
    device: Device,
    file: File,
    user: str,
    version: str,
    add: list[str],
    remove: set[str],
) -> tuple[list[Entry], list[Entry]]:
    """Change one user's file on one device, if it is still as fetched; read back to see it
    took. Returns (added, removed)."""
    me = target_of(device).user
    if not valid_user(user):
        raise ValueError("user names are letters, digits, . _ - (up to 32)")
    async with await keys.connect(target_of(device)) as conn:
        how = await privileged(conn) if user != me else None
        copy = await _read(conn, file, user, me, how)
        if copy.version != version:
            raise RuntimeError("changed since it was fetched: fetch again")
        new, added, removed = apply(file, copy.raw, add, remove)
        if not added and not removed:
            return [], []
        if len(new) > MAX_OUTPUT:
            raise RuntimeError("the file would be too large to handle here")
        command = _command(_WRITE, file, user, me, how) + f" {shlex.quote(version)}"
        r = await conn.run(command, input=new, check=False, timeout=30, encoding=None)
        if r.exit_status != 0:
            err = (r.stderr or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(err[-1][:200] if err else "writing failed")
        after = await _read(conn, file, user, me, how)
    if after.raw != new:
        raise RuntimeError("the file isn't as written when read back")
    return added, removed


def merge(file: File, copies: dict[str, Copy], own: dict[str, str]) -> list[dict[str, Any]]:
    """The entries across columns (a device's user's file, "device/user"): where each is, and
    where it differs from the most common form. `own`: Kastellan's keys by fingerprint (shown,
    never removable)."""
    rows: dict[str, dict[str, Any]] = {}
    for id, copy in copies.items():
        for e in copy.entries:
            row = rows.setdefault(
                e.key,
                {
                    "key": e.key,
                    "type": e.type,
                    "label": e.label[:200],
                    "line": e.line,
                    "fingerprint": e.fingerprint,
                    "kastellan": own.get(e.fingerprint) if file == "authorized_keys" else None,
                    "_variants": {},
                },
            )
            row["_variants"].setdefault(id, (e.variant, e.fingerprint, e.line))
    out = []
    for row in rows.values():
        variants: dict[str, tuple[str, str, str]] = row.pop("_variants")
        common, _ = Counter(v[0] for v in variants.values()).most_common(1)[0]
        sample = next(v for v in variants.values() if v[0] == common)
        row["line"], row["fingerprint"] = sample[2], sample[1]
        row["on"] = {id: "same" if v[0] == common else "differs" for id, v in variants.items()}
        # known_hosts: different keys for one host are a conflict (one of them is wrong).
        row["conflict"] = file == "known_hosts" and len({v[1] for v in variants.values()}) > 1
        out.append(row)
    return sorted(out, key=lambda r: (r["kastellan"] is None, r["label"].lower(), r["key"]))
