"""Kastellan's own backup: its configuration and state, so a new hub can take over from a lost
one. Built on the device backups' store (devbackups.Store): each version once, file contents
deduplicated and encrypted, "what changed" with secrets masked, the oldest versions going.

What is in it: config.yaml (as `@config/config.yaml`), and the data folder: the key rings
(vault.json, itself encrypted under the admins' passwords and recovery keys), the SSH keys and
pinned host keys, secrets kept as files, the OpenWrt and FRITZ!Box certificate pins, the syslog
certificate authority, alarms, backup tokens, the history (optional), the audit log, the device
configuration backups. Not in it: sign-in sessions (people sign in again), the logs (optional),
caches, and the hub backup itself.

The key is not one of the hub's own: a store whose key lives only inside the hub is lost with
it. It comes from a passphrase Kastellan makes once and shows once (`new_passphrase`): scrypt
over it and a salt; the store's folder holds the salt and a check value (`kastellan-backup.json`)
so a restore asks only for the passphrase, and the hub keeps the derived key as the secret
`hub-backup-key` to back up by itself. "Check the passphrase" (`check`) proves you still have it.

Where it goes: the store in `<data>/hub-backup` (on the hub's own disk: it is not the copy
that saves you), copied after every backup to each target: a folder (`path:`, e.g. a NAS share
mounted into the container) or a device over SFTP with Kastellan's SSH login there (`device:` +
`path:`). Every copy is a whole store: `kastellan restore <folder>` takes any of them.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets as random
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import devbackups

log = logging.getLogger(__name__)

ID = "kastellan"  # the store's one "device"
KIND = "kastellan-state"
KEY_SECRET = "hub-backup-key"
HEADER = "kastellan-backup.json"
# Left out of the data folder (relative paths, or their first part).
SKIP = {"sessions.json", "hub-backup", "vulns", "signin-required.tmp", "web"}
LOGS = ("logs.db", "logs.db-wal", "logs.db-shm")
WORDS = 8  # a passphrase: 8 groups of 5 letters and digits, ~200 bits


def _scrypt(passphrase: str, salt: bytes) -> bytes:
    normal = "".join(passphrase.split()).replace("-", "").upper()
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(normal.encode())


def new_passphrase() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
    return "-".join("".join(random.choice(alphabet) for _ in range(5)) for _ in range(WORDS))


def make_key(passphrase: str) -> dict[str, str]:
    """The key (and what a store keeps to recognise it) for a passphrase."""
    salt = os.urandom(16)
    key = _scrypt(passphrase, salt)
    return {
        "salt": base64.b64encode(salt).decode(),
        "key": base64.b64encode(key).decode(),
        "check": hmac.new(key, b"kastellan backup", hashlib.sha256).hexdigest(),
        "made": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def key_from(header: dict[str, Any], passphrase: str) -> bytes:
    """The key a store was made with, from its header and the passphrase; ValueError if wrong."""
    key = _scrypt(passphrase, base64.b64decode(header["salt"]))
    if not hmac.compare_digest(
        hmac.new(key, b"kastellan backup", hashlib.sha256).hexdigest(), header["check"]
    ):
        raise ValueError("that is not this backup's passphrase")
    return key


def collect(data: Path, config: Path, history: bool, logs: bool) -> list[tuple[devbackups.File, bytes]]:
    """The files to keep, read now."""
    out: list[tuple[devbackups.File, bytes]] = []

    def add(path: Path, name: str) -> None:
        try:
            content = path.read_bytes()
            st = path.stat()
        except OSError:
            return
        sha = hashlib.sha256(content).hexdigest()
        out.append(
            (
                devbackups.File(
                    name, sha, len(content), "file", st.st_mode & 0o777, int(st.st_mtime), "", sha
                ),
                content,
            )
        )

    if config.is_file():
        add(config, "@config/config.yaml")
    for path in sorted(data.rglob("*")):
        rel = path.relative_to(data)
        parts = rel.parts
        if not path.is_file() or path.is_symlink():
            continue
        if (
            parts[0] in SKIP
            or parts[0].startswith("hub-backup")
            or rel.name.endswith(".tmp")
            or rel.name.startswith(".")
        ):
            continue
        if not history and parts[0] == "checks":
            continue
        if not logs and rel.name in LOGS:
            continue
        if path.resolve() == config.resolve():
            continue  # config.yaml in the data folder: taken once, as @config
        add(path, rel.as_posix())
    return out


class HubBackup:
    """The hub's store and its copies."""

    def __init__(self, data: Path, key: Callable[[], bytes]) -> None:
        self.folder = data / "hub-backup"
        self.store = devbackups.Store(self.folder, key)
        self.status: dict[str, Any] = {}  # the last run: ok, error, copies per target

    def header(self) -> dict[str, Any] | None:
        try:
            return json.loads((self.folder / HEADER).read_text())
        except (OSError, ValueError):
            return None

    def start(self, made: dict[str, str]) -> None:
        """A store for a new key: one made with another key is set aside (its passphrase still
        opens it), never mixed."""
        if self.folder.exists() and any(self.folder.iterdir()):
            aside = self.folder.with_name(f"hub-backup-{datetime.now(UTC):%Y%m%d-%H%M%S}")
            self.folder.rename(aside)
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        (self.folder / HEADER).write_text(
            json.dumps({"version": 1, "salt": made["salt"], "check": made["check"], "made": made["made"]})
        )

    def take(self, data: Path, config: Path, keep: int, history: bool, logs: bool, by: str | None = None):
        members = collect(data, config, history, logs)
        return self.store.add(ID, KIND, members, keep, by)


def copy_tree(source: Path, target: Path) -> int:
    """Make `target` the same store as `source`: new files copied, gone ones removed (objects
    first, the index last, so a copy is never an index naming objects it lacks). How many
    files were written."""
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    wanted = {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    have = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}
    order = sorted(wanted, key=lambda r: (r.parts[0] != "objects", str(r)))
    for rel in order:
        src, dst = source / rel, target / rel
        if rel in have and rel.parts[0] == "objects":
            continue  # named by their content: the same name, the same bytes
        if rel in have and dst.read_bytes() == src.read_bytes():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.{os.getpid()}.tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        written += 1
    for rel in have - wanted:
        if not rel.name.startswith("."):
            (target / rel).unlink(missing_ok=True)
    return written


async def copy_sftp(source: Path, conn: Any, path: str) -> int:
    """The same as copy_tree, to a folder over SFTP."""
    written = 0
    async with conn.start_sftp_client() as sftp:
        await sftp.makedirs(path, exist_ok=True)
        have: set[str] = set()

        async def walk(rel: str) -> None:
            for entry in await sftp.readdir(f"{path}/{rel}" if rel else path):
                name = entry.filename
                if name in (".", ".."):
                    continue
                sub = f"{rel}/{name}" if rel else name
                if entry.attrs.type == 2:  # a directory
                    await walk(sub)
                else:
                    have.add(sub)

        await walk("")
        wanted = await asyncio.to_thread(
            lambda: {p.relative_to(source).as_posix(): p for p in source.rglob("*") if p.is_file()}
        )
        for rel in sorted(wanted, key=lambda r: (not r.startswith("objects/"), r)):
            if rel in have and rel.startswith("objects/"):
                continue
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if parent:
                await sftp.makedirs(f"{path}/{parent}", exist_ok=True)
            tmp = f"{path}/{rel}.tmp"
            await sftp.put(str(wanted[rel]), tmp)
            with contextlib.suppress(Exception):
                await sftp.remove(f"{path}/{rel}")
            await sftp.rename(tmp, f"{path}/{rel}")
            written += 1
        for rel in have - set(wanted):
            if not rel.rsplit("/", 1)[-1].startswith("."):
                with contextlib.suppress(Exception):
                    await sftp.remove(f"{path}/{rel}")
    return written


# --- Restoring (the CLI: `kastellan restore`) -------------------------------------------------


def restore(
    source: Path, passphrase: str, data: Path, config: Path, version: str | None = None, force: bool = False
) -> dict[str, Any]:
    """Write a version (the newest by default) from a store folder into `data` and `config`.
    Refuses a data folder that already has key rings or keys, unless forced."""
    header = json.loads((source / HEADER).read_text())
    key = key_from(header, passphrase)
    store = devbackups.Store(source, lambda: key)
    snaps = store.snapshots(ID)
    if not snaps:
        raise ValueError("no version in this backup")
    snap = next((s for s in snaps if s.id == version), None) if version else snaps[-1]
    if snap is None:
        raise ValueError(f"no version {version}; there are: {', '.join(s.id for s in snaps)}")
    if not force and any((data / name).exists() for name in ("vault.json", "ssh")):
        raise ValueError(f"{data} already holds a hub's keys; move them away first (or --force)")
    data.mkdir(parents=True, exist_ok=True)
    written = 0
    for f in snap.files:
        if f.type != "file":
            continue
        if f.path.startswith("@config/"):
            target = config
        else:
            target = (data / f.path).resolve()
            if data.resolve() not in target.parents:
                raise ValueError(f"refusing a path outside the data folder: {f.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.restore")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, f.mode or 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(store.get(f.sha))
        tmp.replace(target)
        written += 1
    return {"version": snap.id, "taken": snap.taken, "files": written}


# --- The hub's side ---------------------------------------------------------------------------


def due(status: dict[str, Any], every_hours: float, now: float) -> bool:
    tried = status.get("tried", 0)
    if status.get("error"):
        return now - tried > 3600
    return now - tried > every_hours * 3600


async def run_once(hub: Any, by: str | None = None) -> dict[str, Any]:
    """Back the hub up now and copy it to every target; what happened."""
    cfg = hub.config.hub_backup
    status: dict[str, Any] = {"tried": time.time(), "targets": {}}
    hub.hub_backup.status = status | {"running": True}
    try:
        if hub.hub_backup.header() is None:
            raise ValueError("no passphrase yet: make one on the Backups page")
        snap, new = await asyncio.to_thread(
            hub.hub_backup.take, hub.settings.data, hub.settings.config, cfg.keep, cfg.history, cfg.logs, by
        )
        status |= {"ok": datetime.now(UTC).isoformat(timespec="seconds"), "new": new, "version": snap.id}
        for i, t in enumerate(cfg.targets):
            name = (t.device and f"{t.device}:{t.path}") or t.path
            try:
                if t.device:
                    from .ssh import Target

                    device = hub.config.devices[t.device]
                    if device.ssh is None:
                        raise ValueError(f"{t.device} has no ssh: login")
                    target = Target(device.host, device.ssh.port, device.ssh.user)
                    async with await hub.keys.connect(target) as conn:
                        n = await copy_sftp(hub.hub_backup.folder, conn, t.path)
                else:
                    n = await asyncio.to_thread(copy_tree, hub.hub_backup.folder, Path(t.path))
                status["targets"][name] = {
                    "ok": datetime.now(UTC).isoformat(timespec="seconds"),
                    "written": n,
                }
            except Exception as e:
                status["targets"][name] = {"error": (str(e) or type(e).__name__)[:200], "index": i}
        failed = [n for n, s in status["targets"].items() if s.get("error")]
        status["error"] = f"couldn't copy to {', '.join(failed)}" if failed else None
    except Exception as e:
        status["error"] = (str(e) or type(e).__name__)[:200]
        raise
    finally:
        hub.hub_backup.status = status
        _save_status(hub)
    return status


def _save_status(hub: Any) -> None:
    with contextlib.suppress(OSError):
        (hub.settings.data / "hub-backup-status.json").write_text(json.dumps(hub.hub_backup.status))


def load_status(hub: Any) -> None:
    with contextlib.suppress(OSError, ValueError):
        hub.hub_backup.status = json.loads((hub.settings.data / "hub-backup-status.json").read_text())


async def run(hub: Any) -> None:
    load_status(hub)
    await asyncio.sleep(240)
    while True:
        if (
            not hub.demo
            and hub.hub_backup.header() is not None
            and due(hub.hub_backup.status, hub.config.hub_backup.every_hours, time.time())
        ):
            try:
                await run_once(hub)
            except Exception as e:
                log.info("hub backup: %s", e)
        await asyncio.sleep(600)


def problems(hub: Any, now: float) -> list[Any]:
    """The hub's own backup failing for longer than two periods, or never copied away: a
    warning (on no device: it is Kastellan's own)."""
    from .alarms import Problem, Severity

    st = hub.hub_backup.status
    if hub.demo or hub.hub_backup.header() is None or not st.get("error"):
        return []
    ok = st.get("ok")
    since = datetime.fromisoformat(ok).timestamp() if ok else hub.started.timestamp()
    if now - since < 2 * hub.config.hub_backup.every_hours * 3600:
        return []
    return [
        Problem(
            "hubbackup",
            Severity.WARNING,
            "",
            "Kastellan's own backup",
            "Kastellan's own backup is failing",
            st["error"],
        )
    ]


MAGIC = b"KASTELLAN-BACKUP-1\n"


def encrypted_archive(store: devbackups.Store, header: dict[str, Any], sid: str, key: bytes) -> bytes:
    """One version as a single file to download: its header line, then a .tar.gz of the version
    encrypted with the backup key (`kastellan restore <file>` takes it)."""
    _, tar = store.archive(ID, sid)
    nonce = os.urandom(12)
    head = json.dumps({k: header[k] for k in ("version", "salt", "check")}).encode() + b"\n"
    return MAGIC + head + nonce + AESGCM(key).encrypt(nonce, tar, b"kastellan-backup")


def restore_file(
    source: Path, passphrase: str, data: Path, config: Path, force: bool = False
) -> dict[str, Any]:
    """`kastellan restore <file>`: a downloaded single-file backup."""
    import io
    import tarfile

    raw = source.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError("not a Kastellan backup file")
    head, _, rest = raw[len(MAGIC) :].partition(b"\n")
    key = key_from(json.loads(head), passphrase)
    tar = AESGCM(key).decrypt(rest[:12], rest[12:], b"kastellan-backup")
    if not force and any((data / name).exists() for name in ("vault.json", "ssh")):
        raise ValueError(f"{data} already holds a hub's keys; move them away first (or --force)")
    written = 0
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            if m.name.startswith("@config/"):
                target = config
            else:
                target = (data / m.name).resolve()
                if data.resolve() not in target.parents:
                    raise ValueError(f"refusing a path outside the data folder: {m.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            f = t.extractfile(m)
            content = f.read() if f else b""
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, m.mode or 0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(content)
            written += 1
    return {"files": written}
