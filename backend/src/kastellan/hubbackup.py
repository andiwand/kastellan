"""Encrypted hub snapshots, isolated destination copies, and offline restore.

The backup key comes from a recovery passphrase; the hub keeps only its derived key.
Contents use devbackups.Store; the hub index is authenticated too. Copies go into a separate
kastellan-<key id> folder per passphrase, preserving earlier generations. Restore stages and
validates all files before replacing state. Sessions and caches are excluded; SQLite logs
use the online backup API. External unlock keys must be kept separately.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import hashlib
import hmac
import io
import json
import logging
import os
import secrets as random
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import config_edit, devbackups

log = logging.getLogger(__name__)

ID = "kastellan"  # the store's one "device"
KIND = "kastellan-state"
KEY_SECRET = "hub-backup-key"
INDEX_MAGIC = b"KASTELLAN-INDEX-2\n"
HEADER = "kastellan-backup.json"
# Left out of the data folder (relative paths, or their first part).
SKIP = {"sessions.json", "hub-backup", "vulns", "signin-required.tmp", "web"}
LOGS = ("logs.db", "logs.db-wal", "logs.db-shm")
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 100_000
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


def private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".backup-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_bounded(path: Path, limit: int = MAX_FILE) -> bytes:
    with path.open("rb") as src:
        content = src.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"backup file exceeds {limit // 1048576} MiB: {path.name}")
    return content


def valid_config(content: bytes) -> str:
    from ruamel.yaml import YAML

    from .config import Config

    try:
        text = content.decode("utf-8")
        Config.model_validate(YAML(typ="safe").load(text))
    except Exception as e:
        raise ValueError("backup configuration is invalid") from e
    return text


def collect(data: Path, config: Path, history: bool, logs: bool) -> list[tuple[devbackups.File, bytes]]:
    """Read state without following symlinks; unreadable files fail the backup."""
    out: list[tuple[devbackups.File, bytes]] = []
    total = 0
    stamps = {}

    def add(path: Path, name: str, content: bytes | None = None) -> None:
        nonlocal total
        if content is None:
            st = path.stat()
            content = read_bounded(path)
            if not path.name.endswith((".log", ".jsonl", ".csv")):
                stamps[path] = (st.st_ino, st.st_size, st.st_mtime_ns)
        total += len(content)
        if total > MAX_TOTAL or len(out) >= MAX_FILES:
            raise ValueError("hub backup exceeds its size or file-count limit")
        st = path.stat()
        sha = hashlib.sha256(content).hexdigest()
        out.append((devbackups.File(name, sha, len(content), "file", 0o600, int(st.st_mtime)), content))

    add(config, "@config/config.yaml")  # a missing or unreadable config is never a successful backup
    valid_config(out[0][1])

    def unreadable(error):
        raise error

    for root, dirs, files in os.walk(data, onerror=unreadable):
        kept = []
        for directory in dirs:
            if directory.startswith((".", "hub-backup")) or (
                Path(root) == data and directory in SKIP | ({"checks"} if not history else set())
            ):
                continue
            if (Path(root) / directory).is_symlink():
                raise ValueError("data contains a symlink; back its target up separately")
            kept.append(directory)
        dirs[:] = sorted(kept)
        for name in sorted(files):
            path = Path(root) / name
            rel = path.relative_to(data)
            if name.startswith(".") or name.endswith(".tmp") or rel.parts[0] in SKIP:
                continue
            if name.startswith("hub-backup") or name in LOGS:
                continue
            if path.resolve() == config.resolve():
                continue  # the explicitly configured file was already read, including through its symlink
            if path.is_symlink():
                raise ValueError("data contains a symlink; back its target up separately")
            if not stat.S_ISREG(path.stat().st_mode):
                continue
            add(path, rel.as_posix())
    if logs and (data / "logs.db").exists():
        # SQLite's online backup includes committed WAL pages in one consistent database.
        with tempfile.TemporaryDirectory(prefix="kastellan-logs-") as tmp:
            dest = Path(tmp) / "logs.db"
            with (
                contextlib.closing(
                    sqlite3.connect((data / "logs.db").resolve().as_uri() + "?mode=ro", uri=True)
                ) as src,
                contextlib.closing(sqlite3.connect(dest)) as dst,
            ):
                src.backup(dst)
            add(dest, "logs.db", read_bounded(dest))
    for path, before in stamps.items():
        st = path.stat()
        if before != (st.st_ino, st.st_size, st.st_mtime_ns):
            raise ValueError("state changed while backing up; retry the backup")
    return out


class Store(devbackups.Store):
    """The hub's index is authenticated too: a copy must not be able to rename encrypted files."""

    @devbackups.locked
    def snapshots(self, device: str) -> list[devbackups.Snapshot]:
        if device != ID:
            return []
        path = self._index(device)
        if not path.exists():
            return []
        sealed = read_bounded(path)
        if not sealed.startswith(INDEX_MAGIC):
            raise ValueError("unsigned hub backup index; create a new backup passphrase")
        sealed = sealed[len(INDEX_MAGIC) :]
        raw = AESGCM(self._key()).decrypt(sealed[:12], sealed[12:], b"kastellan-backup-index")
        return [
            devbackups.Snapshot(**(s | {"files": [devbackups.File(**f) for f in s["files"]]}))
            for s in json.loads(raw)["snapshots"]
        ]

    def _save(self, device: str, snaps: list[devbackups.Snapshot]) -> None:
        raw = json.dumps({"snapshots": [asdict(s) for s in snaps]}).encode()
        nonce = os.urandom(12)
        private_write(
            self._index(device),
            INDEX_MAGIC + nonce + AESGCM(self._key()).encrypt(nonce, raw, b"kastellan-backup-index"),
        )

    def get(self, sha: str) -> bytes:
        sealed = read_bounded(self._object(sha), MAX_FILE + 1048576)
        packed = AESGCM(self._key()).decrypt(sealed[:12], sealed[12:], sha.encode())
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as src:
            content = src.read(MAX_FILE + 1)
        if len(content) > MAX_FILE or hashlib.sha256(content).hexdigest() != sha:
            raise ValueError("a stored file is damaged or too large")
        return content


class HubBackup:
    """The hub's store and its copies."""

    def __init__(self, data: Path, key: Callable[[], bytes]) -> None:
        self.folder = data.resolve() / "hub-backup"
        self.store = Store(self.folder, key)
        self.operation = asyncio.Lock()
        self.status: dict[str, Any] = {}  # the last run: ok, error, copies per target

    def header(self) -> dict[str, Any] | None:
        try:
            return json.loads((self.folder / HEADER).read_text())
        except (OSError, ValueError):
            return None

    def start(self, made: dict[str, str]) -> Path | None:
        """Set the old store aside; prepare the replacement before renaming either folder."""
        self.folder.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".hub-backup-", dir=self.folder.parent))
        aside = None
        try:
            private_write(
                staging / HEADER,
                json.dumps({"version": 2, **{k: made[k] for k in ("salt", "check", "made")}}).encode(),
            )
            if self.folder.exists():
                aside = self.folder.with_name(f"hub-backup-{random.token_hex(8)}")
                self.folder.rename(aside)
            try:
                staging.rename(self.folder)
            except BaseException:
                if aside:
                    aside.rename(self.folder)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return aside

    def take(self, data: Path, config: Path, keep: int, history: bool, logs: bool, by: str | None = None):
        header = self.header()
        if not header or header.get("version") != 2:
            raise ValueError("create a new backup passphrase before backing up")
        if not hmac.compare_digest(
            header["check"], hmac.new(self.store._key(), b"kastellan backup", hashlib.sha256).hexdigest()
        ):
            raise ValueError("the backup key does not match its passphrase")
        members = collect(data, config, history, logs)
        return self.store.add(ID, KIND, members, keep, by)


def copy_path(path: str, header: dict[str, Any]) -> str:
    """Each passphrase has its own destination, so old copies remain restorable."""
    return str(PurePosixPath(path) / ("kastellan-" + header["check"][:24]))


def no_symlinks(path: Path) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("backup paths must not contain symlinks")


def copy_files(source: Path) -> list[Path]:
    # Index last. Do not copy stray files or follow links in a backup store.
    files = [source / HEADER, *sorted((source / "objects").glob("*/*")), source / f"{ID}.json"]
    for path in files:
        no_symlinks(path)
        if not path.is_file():
            raise ValueError("incomplete backup store")
    return files


def copy_tree(source: Path, target: Path) -> int:
    """Copy a store into a dedicated folder; never remove unrelated destination files."""
    no_symlinks(target)
    src, dst = source.resolve(), target.resolve()
    if src == dst or src in dst.parents or dst in src.parents:
        raise ValueError("backup source and destination overlap")
    files = copy_files(source)
    header = (source / HEADER).read_bytes()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(target.iterdir()) and (
        not (target / HEADER).is_file() or (target / HEADER).read_bytes() != header
    ):
        raise ValueError("backup destination contains another store or unrelated files")
    written = 0
    for path in files:
        dest = target / path.relative_to(source)
        no_symlinks(dest)
        content = read_bounded(path, MAX_FILE + 1048576)
        if dest.is_file() and read_bounded(dest, MAX_FILE + 1048576) == content:
            continue
        private_write(dest, content)
        written += 1
    # Only obsolete objects belonging to this store are eligible for pruning.
    wanted = {p.relative_to(source) for p in files}
    for path in (target / "objects").glob("*/*"):
        if (
            path.relative_to(target) not in wanted
            and len(path.name) == 64
            and all(c in "0123456789abcdef" for c in path.name)
        ):
            no_symlinks(path)
            path.unlink()
    return written


async def copy_sftp(source: Path, conn: Any, path: str) -> int:
    """Copy through SFTP, with atomic replacement and no deletion of unrelated files."""
    import asyncssh

    files = await blocking(copy_files, source)
    header = await blocking((source / HEADER).read_bytes)
    written = 0
    async with conn.start_sftp_client() as sftp:

        async def safe_directory(folder: str) -> None:
            for parent in reversed((PurePosixPath(folder), *PurePosixPath(folder).parents)):
                name = str(parent)
                try:
                    attrs = await sftp.lstat(name)
                except asyncssh.SFTPNoSuchFile:
                    await sftp.mkdir(name, attrs=asyncssh.SFTPAttrs(permissions=0o700))
                else:
                    if attrs.type != asyncssh.FILEXFER_TYPE_DIRECTORY:
                        raise ValueError("SFTP backup path is not a directory or contains a symlink")

        await safe_directory(path)
        entries = [e.filename for e in await sftp.readdir(path) if e.filename not in (".", "..")]
        if entries:
            if (
                HEADER not in entries
                or (await sftp.lstat(f"{path}/{HEADER}")).type != asyncssh.FILEXFER_TYPE_REGULAR
            ):
                raise ValueError("SFTP backup destination contains unrelated files")
            async with sftp.open(f"{path}/{HEADER}", "rb") as remote:
                if await remote.read(len(header) + 1) != header:
                    raise ValueError("SFTP backup destination belongs to another passphrase")
        wanted = set()
        for file in files:
            rel = file.relative_to(source).as_posix()
            dest = f"{path}/{rel}"
            wanted.add(rel)
            await safe_directory(str(PurePosixPath(dest).parent))
            try:
                attrs = await sftp.lstat(dest)
            except asyncssh.SFTPNoSuchFile:
                attrs = None
            if attrs is not None and attrs.type != asyncssh.FILEXFER_TYPE_REGULAR:
                raise ValueError("SFTP backup destination contains a non-regular file")
            content = await blocking(read_bounded, file, MAX_FILE + 1048576)
            if attrs is not None and attrs.size == len(content):
                async with sftp.open(dest, "rb") as remote:
                    if await remote.read(len(content) + 1) == content:
                        continue
            tmp = dest + "." + random.token_hex(8) + ".tmp"
            try:
                async with sftp.open(tmp, "xb", attrs=asyncssh.SFTPAttrs(permissions=0o600)) as remote:
                    await remote.write(content)
                # Never unlink the old index first: an interrupted copy must keep it readable.
                await sftp.posix_rename(tmp, dest)
            finally:
                with contextlib.suppress(asyncssh.SFTPError):
                    await sftp.remove(tmp)
            written += 1
        # Only prune content-addressed files under objects, after publishing the index.
        for entry in await sftp.readdir(f"{path}/objects"):
            prefix = entry.filename
            if len(prefix) != 2 or any(c not in "0123456789abcdef" for c in prefix):
                continue
            await safe_directory(f"{path}/objects/{prefix}")
            for obj in await sftp.readdir(f"{path}/objects/{prefix}"):
                name = obj.filename
                if (
                    len(name) == 64
                    and all(c in "0123456789abcdef" for c in name)
                    and f"objects/{prefix}/{name}" not in wanted
                ):
                    await sftp.remove(f"{path}/objects/{prefix}/{name}")
    return written


async def blocking(call, *args):
    """A cancelled request waits for its file operation before releasing the backup lock."""
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


# --- Restoring (the CLI: `kastellan restore`) -------------------------------------------------


def restore_members(members, data: Path, config: Path, force: bool) -> int:
    """Validate and stage every file before replacing state. Forced restores replace old state,
    including old sessions, rather than merging keys from two different hubs."""
    data, config = data.absolute(), config.absolute()
    no_symlinks(data)
    no_symlinks(config)
    if not force and ((data.exists() and any(data.iterdir())) or config.exists()):
        raise ValueError("destination already holds data or configuration; use empty folders (or --force)")
    if data == config or config in data.parents:
        raise ValueError("configuration path overlaps the data directory")
    if data == Path(data.anchor):
        raise ValueError("the filesystem root cannot be a restore destination")
    # Keep staging on the destination filesystem, including Docker's mounted /data.
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".kastellan-restore-", dir=data) as work:
        stage, previous = Path(work) / "new", Path(work) / "previous"
        stage.mkdir(mode=0o700)
        previous.mkdir(mode=0o700)
        seen, total, config_text = set(), 0, None
        for name, content in members:
            parts = PurePosixPath(name).parts
            if (
                not parts
                or name.startswith("/")
                or ".." in parts
                or name != PurePosixPath(name).as_posix()
                or "\x00" in name
            ):
                raise ValueError("invalid backup member path")
            if name in seen or any(parent.as_posix() in seen for parent in PurePosixPath(name).parents):
                raise ValueError("duplicate or conflicting backup member path")
            seen.add(name)
            total += len(content)
            if len(content) > MAX_FILE or total > MAX_TOTAL or len(seen) > MAX_FILES:
                raise ValueError("backup exceeds restore limits")
            if name == "@config/config.yaml":
                config_text = content.decode("utf-8")
                continue
            if parts[0].startswith("@") or parts[0] in ("sessions.json", *LOGS[1:]):
                raise ValueError("unexpected backup member")
            target = stage / name
            if target.exists() or target.is_symlink():
                raise ValueError("conflicting backup member path")
            private_write(target, content)
        if config_text is None:
            raise ValueError("backup has no configuration")
        valid_config(config_text.encode())
        try:
            relative_config = config.relative_to(data)
        except ValueError:
            relative_config = None
        if relative_config is not None and (stage / relative_config).exists():
            raise ValueError("data member conflicts with configuration path")
        old_config = config.read_text() if config.is_file() else ""
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        moved, installing = [], False
        try:
            for item in data.iterdir():
                if item == Path(work):
                    continue
                item.rename(previous / item.name)
                moved.append(item.name)
            installing = True
            for item in stage.iterdir():
                item.rename(data / item.name)
            config_edit.write(config, data / "config-backups", old_config, config_text)
        except BaseException:
            if installing:
                for item in data.iterdir():
                    if item == Path(work):
                        continue
                    if item.is_dir() and not item.is_symlink():
                        shutil.rmtree(item)
                    else:
                        item.unlink(missing_ok=True)
            for name in moved:
                (previous / name).rename(data / name)
            raise
    return len(seen)


def restore(
    source: Path, passphrase: str, data: Path, config: Path, version: str | None = None, force: bool = False
) -> dict[str, Any]:
    header = json.loads((source / HEADER).read_text())
    key = key_from(header, passphrase)
    if header.get("version") != 2:
        raise ValueError("unsigned backup store: download a .kbak from the original hub or make a new backup")
    store = Store(source, lambda: key)
    snaps = store.snapshots(ID)
    if not snaps:
        raise ValueError("no version in this backup")
    snap = next((s for s in snaps if s.id == version), None) if version else snaps[-1]
    if snap is None:
        raise ValueError("no such backup version")
    if any(f.type != "file" for f in snap.files):
        raise ValueError("unexpected non-file backup member")
    written = restore_members(((f.path, store.get(f.sha)) for f in snap.files), data, config, force)
    return {"version": snap.id, "taken": snap.taken, "files": written}


# --- The hub's side ---------------------------------------------------------------------------


def due(status: dict[str, Any], every_hours: float, now: float) -> bool:
    tried = status.get("tried", 0)
    if status.get("error"):
        return now - tried > 3600
    return now - tried > every_hours * 3600


async def run_once(hub: Any, by: str | None = None) -> dict[str, Any]:
    """Serialize snapshot, copy and key rotation; a disconnected browser cannot overlap them."""
    async with hub.hub_backup.operation:
        return await _run_once(hub, by)


async def _run_once(hub: Any, by: str | None) -> dict[str, Any]:
    cfg = hub.config.hub_backup
    status: dict[str, Any] = {"tried": time.time(), "targets": {}, "ok": hub.hub_backup.status.get("ok")}
    hub.hub_backup.status = status | {"running": True}
    try:
        header = hub.hub_backup.header()
        if header is None:
            raise ValueError("no passphrase yet: make one on the Backups page")
        for target in cfg.targets:
            if not target.device:
                path, data = await blocking(Path(target.path).resolve), hub.settings.data.resolve()
                if path == data or path in data.parents or data in path.parents:
                    raise ValueError("a backup destination must be outside the hub's data folder")
                if hub.settings.config.resolve().is_relative_to(path):
                    raise ValueError("a backup destination must not contain the hub's configuration")

        # Device-backup pruning must not delete objects while we read their indexes and files.
        def take():
            with hub.device_backups._lock, hub.vault._lock:
                return hub.hub_backup.take(
                    hub.settings.data, hub.settings.config, cfg.keep, cfg.history, cfg.logs, by
                )

        snap, new = await blocking(take)
        status |= {
            "local_ok": datetime.now(UTC).isoformat(timespec="seconds"),
            "new": new,
            "version": snap.id,
        }
        for t in cfg.targets:
            name = f"{t.device}:{t.path}" if t.device else t.path
            destination = copy_path(t.path, header)
            try:
                async with asyncio.timeout(180):
                    if t.device:
                        from .ssh import Target

                        device = hub.config.devices[t.device]
                        if device.ssh is None:
                            raise ValueError(f"{t.device} has no ssh: login")
                        target = Target(device.host, device.ssh.port, device.ssh.user)
                        async with await hub.keys.connect(target) as conn:
                            n = await copy_sftp(hub.hub_backup.folder, conn, destination)
                    else:
                        n = await blocking(copy_tree, hub.hub_backup.folder, Path(destination))
                status["targets"][name] = {
                    "ok": datetime.now(UTC).isoformat(timespec="seconds"),
                    "written": n,
                    "path": destination,
                }
            except Exception as e:
                status["targets"][name] = {"error": (str(e) or type(e).__name__)[:200], "path": destination}
        failed = [n for n, st in status["targets"].items() if st.get("error")]
        status["error"] = f"couldn't copy to {', '.join(failed)}" if failed else None
        if cfg.targets and not failed:
            status["ok"] = datetime.now(UTC).isoformat(timespec="seconds")
    except Exception as e:
        status["error"] = (str(e) or type(e).__name__)[:200]
        raise
    finally:
        hub.hub_backup.status = status
        _save_status(hub)
    return status


def _save_status(hub: Any) -> None:
    with contextlib.suppress(OSError):
        private_write(
            hub.hub_backup.folder.parent / "hub-backup-status.json",
            json.dumps(hub.hub_backup.status).encode(),
        )


def load_status(hub: Any) -> None:
    with contextlib.suppress(OSError, ValueError):
        hub.hub_backup.status = json.loads((hub.settings.data / "hub-backup-status.json").read_text())


async def run(hub: Any) -> None:
    if hub.demo:
        return
    load_status(hub)
    await asyncio.sleep(240)
    while True:
        if (
            not hub.demo
            and hub.hub_backup.header() is not None
            and not hub.hub_backup.operation.locked()
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
    header = hub.hub_backup.header()
    if hub.demo or header is None:
        return []
    error = st.get("error") or (
        "no external backup destination configured" if not hub.config.hub_backup.targets else None
    )
    ok = st.get("ok")
    since = datetime.fromisoformat(ok or header["made"]).timestamp()
    if now - since < 2 * hub.config.hub_backup.every_hours * 3600:
        return []
    return [
        Problem(
            "hubbackup",
            Severity.WARNING,
            "",
            "Kastellan's own backup",
            "Kastellan's own backup is failing",
            error or "no recent successful backup copy",
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
    """Restore a downloaded archive, authenticating and staging it before changing any files."""
    raw = read_bounded(source, MAX_TOTAL + MAX_FILES * 1024 + 1048576)
    if not raw.startswith(MAGIC):
        raise ValueError("not a Kastellan backup file")
    head, _, rest = raw[len(MAGIC) :].partition(b"\n")
    header = json.loads(head)
    if header.get("version") not in (1, 2):
        raise ValueError("unsupported backup version")
    key = key_from(header, passphrase)
    packed = AESGCM(key).decrypt(rest[:12], rest[12:], b"kastellan-backup")
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as zipped:
        tar = zipped.read(MAX_TOTAL + MAX_FILES * 1024 + 1)
    if len(tar) > MAX_TOTAL + MAX_FILES * 1024:
        raise ValueError("expanded backup is too large")
    with tarfile.open(fileobj=io.BytesIO(tar), mode="r:") as archive:

        def members():
            for m in archive:
                if not m.isfile() or m.size > MAX_FILE:
                    raise ValueError("unexpected or oversized backup member")
                member = archive.extractfile(m)
                assert member is not None
                yield m.name, member.read(MAX_FILE + 1)

        written = restore_members(members(), data, config, force)
    return {"files": written}
