"""Device configuration backups: taken from the devices that allow it, kept encrypted, each
version only once, and compared with the one before.

What a backup is, per kind of device:
- OpenWrt (`openwrt-sysupgrade`): `sysupgrade -b`'s .tar.gz (the `backup` ability), kept file by
  file; restoring it is uploading the rebuilt archive in LuCI (System → Backup → Restore).
- FRITZ!Box (`fritzbox-export`): its settings export (one text file, its passwords encrypted with
  the export password Kastellan keeps as the secret `fritzbox-export-<id>`; restoring it in the
  FRITZ!Box asks for that password).

Storage (`<data>/device-backups/`): every file's content once, by its SHA-256, encrypted with
AES-GCM under one key (the secret `device-backups`, made on first use: with a key ring it is in
the ring), the hash bound in as associated data; per device an index of versions, each a list of
files. A backup whose files are the same as the newest version's adds no version: that version's
`checked` moves on ("the same since ..."). Content must match exactly: changes to a FRITZ!Box's
encrypted values may be password changes and must be retained.

Comparing two versions shows which files came, went or changed, and for text files the lines;
known password and key options become a fingerprint (an HMAC with
a key of Kastellan's), so a change is visible but not the value, and files that are secrets as a
whole (shadow, private keys), plus unknown formats, show only that they changed.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets as random
import shlex
import tarfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import ID
from .config_edit import masked_text

log = logging.getLogger(__name__)

MAX_FILE = 16 * 1024 * 1024
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_MEMBERS = 10_000
MAX_DIFF_TEXT = 512 * 1024

# Files that are a secret as a whole: only "changed" is shown.
SECRET_FILE = re.compile(
    r"(^|/)(shadow|passwd-|gshadow|\.htpasswd)$|(^|/)etc/dropbear/|\.key$|_key$|(^|/)etc/wireguard/"
    r"|(^|/)etc/openvpn/|(^|/)etc/ssl/private/|(^|/)etc/acme/.*\.key|/\.ssh/|^export-password\.txt$"
    # Kastellan's own data (hubbackup.py): key rings, secrets as files, its SSH and push keys.
    r"|^vault\.json$|^secrets/|^ssh/[^/]*_ed25519|^push/|^sessions\.json$"
)
TEXT_CONFIGS = {f"etc/config/{name}" for name in ("wireless", "network", "system", "dhcp", "firewall")}
TEXT_CONFIGS |= {"@config/config.yaml"}  # Kastellan's own (hubbackup.py): passwords masked like the rest
# uci options (and similar `name value` lines) whose values are secrets.
SECRET_OPTIONS = (
    "key|key[1-4]|password|passwd|sae_password|private_key|preshared_key|psk|wpa_psk|auth_secret"
    "|acct_secret|priv_key_pwd|secret|token|api_key|apikey|pass|mesh_key|shared_secret|pin"
)
UCI_SECRET = re.compile(rf"^(\s*(?:option|list)\s+['\"]?(?:{SECRET_OPTIONS})['\"]?\s+)(.*)$", re.I)
KV_SECRET = re.compile(rf"^(\s*(?:{SECRET_OPTIONS})\s*[=:]\s*)(.*)$", re.I)
PRIVATE_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
FRITZ_SECRET = re.compile(r"\$\$\$\$[A-Za-z0-9]+")


@dataclass
class File:
    path: str
    sha: str  # of the content
    size: int
    type: str = "file"  # file, dir, symlink
    mode: int = 0o644
    mtime: int = 0
    link: str = ""
    same: str = ""  # only in indexes written before; never set or used now
    uid: int = 0
    gid: int = 0
    uname: str = ""
    gname: str = ""

    def key(self) -> tuple:
        return (self.path, self.type, self.sha, self.mode, self.link, self.uid, self.gid)


@dataclass
class Snapshot:
    id: str
    kind: str
    taken: str  # when this version was first seen
    checked: str  # when it was last seen the same
    files: list[File] = field(default_factory=list)
    by: str | None = None  # who asked (None: on schedule)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "taken": self.taken,
            "checked": self.checked,
            "files": sum(1 for f in self.files if f.type == "file"),
            "size": sum(f.size for f in self.files if f.type == "file"),
            "by": self.by,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def unpack_tar(data: bytes) -> list[tuple[File, bytes]]:
    """A .tar.gz's members, read in memory (never extracted): files with their content."""
    out: list[tuple[File, bytes]] = []
    # Bound decompression itself, including tar metadata and padding.
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        raw = compressed.read(MAX_ARCHIVE + 1)
    if len(raw) > MAX_ARCHIVE:
        raise ValueError("the expanded backup is too large")
    seen: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for i, m in enumerate(tar):
            if i >= MAX_MEMBERS:
                raise ValueError("too many files in the backup")
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise ValueError("unsafe path in the backup")
            path = "/".join(p for p in m.name.split("/") if p not in ("", "."))
            if not path and m.isdir():
                continue
            if not path or path in seen:
                raise ValueError("empty or duplicate path in the backup")
            seen.add(path)
            if m.isfile():
                if m.size > MAX_FILE:
                    raise ValueError(f"{path} is larger than {MAX_FILE // 2**20} MB")
                f = tar.extractfile(m)
                content = f.read() if f else b""
                sha = hashlib.sha256(content).hexdigest()
                out.append((File(path, sha, len(content), "file", m.mode, int(m.mtime)), content))
            elif m.isdir():
                out.append((File(path, "", 0, "dir", m.mode, int(m.mtime)), b""))
            elif m.issym():
                out.append((File(path, "", 0, "symlink", m.mode, int(m.mtime), m.linkname), b""))
            elif m.islnk():
                if m.linkname.startswith("/") or ".." in m.linkname.split("/"):
                    raise ValueError("unsafe hard link in the backup")
                out.append((File(path, "", 0, "hardlink", m.mode, int(m.mtime), m.linkname), b""))
            else:
                raise ValueError("unsupported file type in the backup")
            out[-1][0].uid, out[-1][0].gid = m.uid, m.gid
            out[-1][0].uname, out[-1][0].gname = m.uname, m.gname
    if not out:
        raise ValueError("the backup is empty")
    return out


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return call


class Store:
    """The versions of every device's backups, and their files (encrypted)."""

    def __init__(self, folder: Path, key: Callable[[], bytes]) -> None:
        self.folder = folder
        self._key = key  # 32 bytes, from the secrets
        self._lock = threading.RLock()

    # --- objects ------------------------------------------------------------------------------

    def unchanged(self) -> threading.RLock:
        """Held: no version is added or removed meanwhile (a consistent copy of the store)."""
        return self._lock

    def _object(self, sha: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("not a hash")
        return self.folder / "objects" / sha[:2] / sha

    def put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        path = self._object(sha)
        if path.exists():
            return sha
        path.parent.mkdir(parents=True, exist_ok=True)
        nonce = os.urandom(12)
        sealed = nonce + AESGCM(self._key()).encrypt(nonce, gzip.compress(content), sha.encode())
        tmp = path.with_name(f".{sha}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(sealed)
        tmp.replace(path)
        return sha

    def get(self, sha: str) -> bytes:
        sealed = self._object(sha).read_bytes()
        content = gzip.decompress(AESGCM(self._key()).decrypt(sealed[:12], sealed[12:], sha.encode()))
        if hashlib.sha256(content).hexdigest() != sha:
            raise ValueError("a stored file is damaged")
        return content

    # --- the index per device -----------------------------------------------------------------

    def _index(self, device: str) -> Path:
        if not ID.fullmatch(device):
            raise ValueError("not a device id")
        return self.folder / f"{device}.json"

    def snapshots(self, device: str) -> list[Snapshot]:
        try:
            data = json.loads(self._index(device).read_text())
        except FileNotFoundError:
            return []
        return [
            Snapshot(**{**s, "files": [File(**f) for f in s.get("files", [])]})
            for s in data.get("snapshots", [])
        ]

    def _save(self, device: str, snaps: list[Snapshot]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        path = self._index(device)
        data = {
            "snapshots": [
                {
                    **{k: v for k, v in s.__dict__.items() if k != "files"},
                    "files": [f.__dict__ for f in s.files],
                }
                for s in snaps
            ]
        }
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        tmp.replace(path)

    def snapshot(self, device: str, sid: str) -> Snapshot:
        for s in self.snapshots(device):
            if s.id == sid:
                return s
        raise KeyError(sid)

    def add(
        self, device: str, kind: str, members: list[tuple[File, bytes]], keep: int, by: str | None = None
    ) -> tuple[Snapshot, bool]:
        """A backup's files; (its version, whether it is new). The same as the newest version:
        that one is "checked" again instead."""
        with self._lock:
            for f, content in members:
                if f.type == "file":
                    f.sha = self.put(content)
            files = sorted((f for f, _ in members), key=lambda f: f.path)
            snaps = self.snapshots(device)
            now = _now()
            if (
                snaps
                and snaps[-1].kind == kind
                and [f.key() for f in snaps[-1].files] == [f.key() for f in files]
            ):
                snaps[-1].checked = now
                snap, new = snaps[-1], False
            else:
                snap, new = Snapshot(random.token_hex(6), kind, now, now, files, by), True
                snaps.append(snap)
            dropped = snaps[:-keep] if len(snaps) > keep else []
            snaps = snaps[-keep:]
            self._save(device, snaps)
            self._collect(dropped)
            return snap, new

    def _collect(self, dropped: list[Snapshot]) -> None:
        """Objects no version of any device names any more."""
        if not dropped:
            return
        used: set[str] = set()
        for index in self.folder.glob("*.json"):
            for s in self.snapshots(index.stem):
                used |= {f.sha for f in s.files if f.sha}
        for s in dropped:
            for f in s.files:
                if f.sha and f.sha not in used:
                    self._object(f.sha).unlink(missing_ok=True)

    def forget(self, device: str) -> None:
        with self._lock:
            snaps = self.snapshots(device)
            self._index(device).unlink(missing_ok=True)
            self._collect(snaps)

    # --- what comes out -----------------------------------------------------------------------

    @locked
    def archive(self, device: str, sid: str) -> tuple[str, bytes]:
        """A version as the device takes it back: (file name, bytes)."""
        snap = self.snapshot(device, sid)
        stamp = snap.taken[:10]
        if snap.kind == "fritzbox-export":
            f = next(f for f in snap.files if f.path == "fritzbox.export")
            password = next((f for f in snap.files if f.path == "export-password.txt"), None)
            if password is None:  # older snapshots: password is in fritzbox-export-<device>
                return f"{device}-{stamp}.export", self.get(f.sha)
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(f"{device}-{stamp}.export", self.get(f.sha))
                archive.writestr("export-password.txt", self.get(password.sha))
            return f"{device}-{stamp}.zip", out.getvalue()
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as tar:
            for f in snap.files:
                info = tarfile.TarInfo(f.path)
                info.mode, info.mtime = f.mode, f.mtime
                info.uid, info.gid, info.uname, info.gname = f.uid, f.gid, f.uname, f.gname
                if f.type == "dir":
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                elif f.type in ("symlink", "hardlink"):
                    info.type = tarfile.SYMTYPE if f.type == "symlink" else tarfile.LNKTYPE
                    info.linkname = f.link
                    tar.addfile(info)
                else:
                    content = self.get(f.sha)
                    info.size = len(content)
                    tar.addfile(info, io.BytesIO(content))
        return f"backup-{device}-{stamp}.tar.gz", out.getvalue()

    @locked
    def diff(self, device: str, older: str, newer: str, salt: bytes) -> dict[str, Any]:
        """What changed from one version to another; secrets masked (see the top)."""
        a = {f.path: f for f in self.snapshot(device, older).files}
        b = {f.path: f for f in self.snapshot(device, newer).files}
        added = sorted(b.keys() - a.keys())
        removed = sorted(a.keys() - b.keys())
        changed = []
        for p in sorted(a.keys() & b.keys()):
            fa, fb = a[p], b[p]
            if fa.key() == fb.key():
                continue
            entry: dict[str, Any] = {"path": p, "size": [fa.size, fb.size]}
            if fa.type != "file" or fb.type != "file":
                entry["only_secrets"] = True  # metadata change; no text content to compare
            elif secret_file(p):
                entry["secret"] = True
            else:
                ta, tb = _text(self.get(fa.sha), p), _text(self.get(fb.sha), p)
                if ta is None or tb is None:
                    entry["binary"] = True
                else:
                    lines = difflib.unified_diff(
                        mask(p, ta, salt).splitlines(),
                        mask(p, tb, salt).splitlines(),
                        "before",
                        "after",
                        n=2,
                        lineterm="",
                    )
                    entry["diff"] = list(lines)[2:][:2000]
                    if not entry["diff"]:
                        entry["only_secrets"] = True  # e.g. a FRITZ!Box's re-encrypted passwords
            changed.append(entry)
        return {"added": added, "removed": removed, "changed": changed}


def secret_file(path: str) -> bool:
    """Unknown file formats are hidden too: a filename cannot prove they contain no secrets."""
    return bool(SECRET_FILE.search(path)) or path not in TEXT_CONFIGS | {"fritzbox.export"}


def _text(content: bytes, path: str = "") -> str | None:
    if len(content) > MAX_DIFF_TEXT or b"\0" in content[:8192]:
        return None
    try:
        return content.decode("latin-1" if path == "fritzbox.export" else "utf-8")
    except UnicodeDecodeError:
        return None


def fingerprint(value: str | bytes, salt: bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return "[secret " + hmac.new(salt, data, hashlib.sha256).hexdigest()[:8] + "]"


def mask(path: str, text: str, salt: bytes) -> str:
    """The text with every secret value replaced by its fingerprint."""
    if secret_file(path):
        return fingerprint(text, salt)
    if path == "@config/config.yaml":
        return masked_text(text) or fingerprint(text, salt)
    if path in TEXT_CONFIGS:
        # A multiline value or an unfamiliar statement needs a real UCI parser;
        # hide the whole file rather than accidentally showing its continuation.
        for line in text.splitlines():
            try:
                words = shlex.split(line, comments=True)
            except ValueError:
                return fingerprint(text, salt)
            if words and (words[0] not in ("config", "option", "list") or len(words) > 3):
                return fingerprint(text, salt)
    text = PRIVATE_BLOCK.sub(lambda m: fingerprint(m.group(0), salt), text)
    text = FRITZ_SECRET.sub("[encrypted]", text)
    out = []
    for line in text.splitlines():
        if m := UCI_SECRET.match(line):
            line = f"{m.group(1)}'{fingerprint(m.group(2), salt)}'"
        elif m := KV_SECRET.match(line):
            line = f"{m.group(1)}{fingerprint(m.group(2).strip(), salt)}"
        out.append(line)
    return "\n".join(out)


def new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# --- Taking them (the hub's task and the "back up now" button) --------------------------------

EVERY_CHECK = 600  # how often the hub looks which device is due
DENIED_AGAIN = 24 * 3600


def kind_of(device: Any) -> str | None:
    if device.openwrt is not None:
        return "openwrt-sysupgrade"
    if device.fritzbox is not None:
        return "fritzbox-export"
    return None


async def fetch(hub: Any, id: str, at_the_box: bool = False) -> list[tuple[File, bytes]]:
    """A device's backup now, as files. `at_the_box`: someone asked and can confirm a FRITZ!Box's
    second factor (its export asks for it), so it is waited for."""
    device = hub.config.devices[id]
    if device.openwrt is not None:
        from .openwrt import client as ow

        data = await ow.backup(hub.ubus(id))
        return await asyncio.to_thread(unpack_tar, data)
    if device.fritzbox is not None:
        from .fritzbox import client as fb

        password = hub.own_secret(f"fritzbox-export-{id}")
        box = hub.fritz(id)
        content = await fb.confirmed(
            box, lambda: fb.config_export(box, password), wait=90 if at_the_box else 0
        )
        sha = hashlib.sha256(content).hexdigest()
        password_bytes = password.encode()
        return [
            (File("fritzbox.export", sha, len(content), "file", 0o600), content),
            (File("export-password.txt", "", len(password_bytes), "file", 0o600), password_bytes),
        ]
    raise ValueError(f"{id} has no way to take a backup (an OpenWrt or FRITZ!Box login)")


def _denied(e: Exception) -> bool:
    from .openwrt import client as ow

    return (isinstance(e, ow.UbusError) and e.code in (6, ow.DENIED)) or bool(getattr(e, "denied", False))


SECOND_FACTOR_NOTE = (
    "FRITZ!OS asks for its second factor (a button on the box) for every settings export, so it"
    " isn't backed up by itself: use “Back up now” and press the button when asked, or turn that"
    " confirmation off in FRITZ!OS (System → FRITZ!Box Users → Login to the Home Network)"
)


async def take(hub: Any, id: str, by: str | None = None) -> dict[str, Any]:
    """Back one device up now; what happened (also kept in hub.backup_status)."""
    kind = kind_of(hub.config.devices[id])
    now = _now()
    state = dict(hub.backup_status.get(id) or {})
    state["tried"] = now
    try:
        members = await fetch(hub, id, at_the_box=by is not None)
        snap, new = await asyncio.to_thread(
            hub.device_backups.add, id, kind, members, hub.config.device_backups.keep, by
        )
    except Exception as e:
        denied = _denied(e)
        second = getattr(e, "code", None) == 866 and kind == "fritzbox-export"
        state |= {
            "error": "not allowed: tick “Download its configuration backup” in its setup"
            if denied and kind == "openwrt-sysupgrade"
            else SECOND_FACTOR_NOTE
            if second and by is None
            else (str(e) or type(e).__name__)[:200],
            # Not a failure to warn about: it waits for someone at the box (or that setting).
            "denied": denied or second,
        }
        hub.backup_status[id] = state
        raise
    state |= {"ok": now, "error": None, "denied": False, "new": new, "snapshot": snap.id}
    hub.backup_status[id] = state
    return {"snapshot": snap.view(), "new": new}


def due(hub: Any, id: str, now: datetime) -> bool:
    state = hub.backup_status.get(id) or {}
    tried = state.get("tried")
    if not tried:
        return True
    age = (now - datetime.fromisoformat(tried)).total_seconds()
    if state.get("denied"):
        return age > DENIED_AGAIN
    if state.get("error"):
        return age > 3600  # a failure is tried again within the hour
    return age > hub.config.device_backups.every_hours * 3600


async def run(hub: Any) -> None:
    """Every device whose login allows it, every `device_backups.every_hours`."""
    await asyncio.sleep(180)  # after the first polls
    while True:
        if not hub.demo:
            now = datetime.now(UTC)
            for id, device in list(hub.config.devices.items()):
                if kind_of(device) and due(hub, id, now):
                    try:
                        await take(hub, id)
                    except Exception as e:
                        log.info("backup of %s: %s", id, (str(e) or type(e).__name__)[:200])
        await asyncio.sleep(EVERY_CHECK)


def problems(hub: Any, now: datetime) -> list[Any]:
    """A device whose backups fail (not: one that doesn't allow them) for longer than two
    periods: a warning."""
    from .alarms import Problem, Severity

    out = []
    for id, state in hub.backup_status.items():
        if id not in hub.config.devices or state.get("denied") or not state.get("error"):
            continue
        since = state.get("ok") or hub.started.isoformat()
        hours = (now - datetime.fromisoformat(since)).total_seconds() / 3600
        if hours > 2 * hub.config.device_backups.every_hours:
            label = hub.config.label(id)
            out.append(
                Problem(
                    f"devbackup:{id}",
                    Severity.WARNING,
                    id,
                    f"{label}: configuration backup",
                    f"{label}: its configuration backup has failed for {hours:.0f} h",
                    state["error"],
                )
            )
    return out
