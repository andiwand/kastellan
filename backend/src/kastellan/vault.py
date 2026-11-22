"""Key rings: Kastellan's secrets and people's logins, encrypted, each ring unlocked its own way.

`<data>/vault.json` holds the rings. Each ring has its own random ring key (AES-256-GCM; a
random nonce each time; an entry's id, ring and kind bound in as associated data, so an entry
can't be moved to another ring or turned into another kind by editing the file). A ring key is
never on disk as is: it is kept only wrapped, once per way to unlock the ring (a "slot"):

- password: an admin's password (scrypt), opened when they sign in;
- recovery: a random recovery key, shown once when the ring is made;
- keyfile / env / config: a random key read at start from a file, an environment variable, or
  config.yaml itself, so the ring opens by itself (how much that protects is the user's choice:
  a key in config.yaml opens the ring for whoever has a copy of the config folder).

Unwrapped, a ring key lives only in this process's memory. When it is forgotten again is the
ring's policy (config.yaml `vault:`, `policy()`): at Lock or a restart, when the last admin
signs out, or after some idle minutes. A ring's policy also says whether Kastellan may use its
entries by itself (background work): the `kastellan` ring yes, `people` (people's own logins)
never — only an action a signed-in admin starts may read one (`get(..., by=...)`).

Public halves (SSH public keys) and entry names are kept beside the encryption: setup commands
and "where it is used" need them while a ring is locked. A login's user name and notes are
sealed with its secret. Opening a ring checks each SSH key's stored public half against its
private key.

What this protects against: reading `vault.json` (a backup, a stolen disk, a copy of the data
folder) reveals no secret without a way to open its ring. It does not protect against someone
who can write the data folder while the hub runs (they could also change sessions.json): that
person is an admin already.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

if TYPE_CHECKING:
    from .config import VaultSettings

Kind = Literal["password", "token", "ssh-key", "other"]
ID = re.compile(r"^[a-z0-9][a-z0-9_./-]{0,127}$")
RING = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
KASTELLAN, PEOPLE = "kastellan", "people"
DEFAULT_RINGS = {KASTELLAN: "Kastellan", PEOPLE: "People's logins"}
AUTO = ("keyfile", "env", "config")  # ways a ring opens by itself, at start

# scrypt for slots: stronger than the login hashes (a leaked vault.json alone is an offline
# target for an admin's password): ~128 MB and a few hundred ms per try.
_N, _R, _P = 2**17, 8, 1


class Locked(Exception):
    def __init__(self, ring: str = KASTELLAN) -> None:
        super().__init__(f"the key ring {ring!r} is locked: an admin signing in unlocks it")
        self.ring = ring


class WrongKey(Exception):
    """The password or key doesn't open a slot."""


class NotForKastellan(Exception):
    """An entry Kastellan may not use by itself (people's logins): only an admin's own action may."""


class Damaged(Exception):
    """vault.json can't be read (changed by hand, a broken disk)."""


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _kek(secret: str, salt: bytes) -> bytes:
    return hashlib.scrypt(secret.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=256 * 1024 * 1024, dklen=32)


def _mac(key: bytes, secret: str) -> str:
    """Tells a random key (key file, env, config) a slot was made with, without scrypt: only for
    256-bit random keys, never passwords (a fast check would make them easy to guess)."""
    return hmac.new(key, secret.encode(), hashlib.sha256).hexdigest()[:32]


def _seal(key: bytes, plain: bytes, aad: bytes) -> dict[str, str]:
    nonce = os.urandom(12)
    return {"nonce": _b64(nonce), "data": _b64(AESGCM(key).encrypt(nonce, plain, aad))}


def _open(key: bytes, box: dict[str, str], aad: bytes) -> bytes:
    return AESGCM(key).decrypt(base64.b64decode(box["nonce"]), base64.b64decode(box["data"]), aad)


def _aad(*parts: str) -> bytes:
    return json.dumps(list(parts)).encode()


def new_recovery_key() -> str:
    """160 random bits, in groups of four for writing down (base32: no 0/O or 1/l mix-ups)."""
    raw = base64.b32encode(os.urandom(20)).decode()
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def new_ring_key() -> str:
    """A key for a key file, an environment variable or config.yaml: 256 random bits."""
    return secrets.token_urlsafe(32)


def _normal_recovery(key: str) -> str:
    return re.sub(r"[\s-]", "", key).upper()


# --- Policy: config.yaml `vault:` (level presets, per-ring choices) ------------------------------

Way = tuple[str, str]  # ("password", "") | ("keyfile", path) | ("env", VAR) | ("config", "")


@dataclass(frozen=True)
class Policy:
    unlock: tuple[Way, ...]  # besides the recovery key, which every ring has
    stays: str  # "lock" (until Lock or a restart) | "signed_in" | "idle:<minutes>"
    background: bool  # may Kastellan use its entries by itself

    @property
    def idle(self) -> float | None:
        return float(self.stays.split(":")[1]) * 60 if self.stays.startswith("idle:") else None


# What each level means for the two rings Kastellan makes (and for rings people add).
LEVELS: dict[str, dict[str, Policy]] = {
    # Kastellan's ring opens by itself at start (a key in config.yaml unless a key file or an
    # environment variable is chosen): everything works after a power cut.
    "convenient": {
        KASTELLAN: Policy((("password", ""), ("config", "")), "lock", True),
        PEOPLE: Policy((("password", ""),), "signed_in", False),
        "*": Policy((("password", ""),), "signed_in", False),
    },
    # Nothing opens by itself; once an admin signs in, Kastellan's ring stays open until a restart.
    "balanced": {
        KASTELLAN: Policy((("password", ""),), "lock", True),
        PEOPLE: Policy((("password", ""),), "signed_in", False),
        "*": Policy((("password", ""),), "signed_in", False),
    },
    # As balanced, and people's logins close again after 15 minutes without use.
    "strict": {
        KASTELLAN: Policy((("password", ""),), "lock", True),
        PEOPLE: Policy((("password", ""),), "idle:15", False),
        "*": Policy((("password", ""),), "idle:15", False),
    },
}


def ways_of(items: list[Any]) -> tuple[Way, ...]:
    """config.yaml's `unlock:` list as ways: `password`, `config`, `{keyfile: path}`, `{env: VAR}`."""
    out: list[Way] = []
    for item in items:
        way: Way = (item, "") if isinstance(item, str) else next(iter(item.items()))
        if way not in out:
            out.append(way)
    return tuple(out)


def policy(settings: VaultSettings | None, ring: str) -> Policy:
    """A ring's policy: its level's, with what config.yaml says for this ring on top."""
    level = LEVELS[settings.level if settings else "balanced"]
    base = level.get(ring, level["*"])
    own = settings.rings.get(ring) if settings else None
    if own is None:
        return base
    stays = own.stays_unlocked or base.stays
    return Policy(
        ways_of(own.unlock) if own.unlock is not None else base.unlock,
        "lock" if stays == "restart" else stays,
        # People's logins are never Kastellan's to use, whatever the file says.
        False if ring == PEOPLE else (own.background if own.background is not None else base.background),
    )


def read_keyfile(path: str) -> tuple[str, str | None]:
    """A key file's key, and a warning if others may read it."""
    p = Path(path)
    mode = p.stat().st_mode
    warning = f"{path} can be read by others (chmod 600)" if mode & (stat.S_IRWXG | stat.S_IRWXO) else None
    return p.read_text().strip(), warning


# --- The rings ---------------------------------------------------------------------------------


@dataclass
class Entry:
    id: str
    ring: str
    kind: Kind
    name: str
    user: str
    notes: str
    created: float
    changed: float
    public: str  # an SSH key's public half; "" otherwise
    # A login's devices: the only ones a terminal may send it to (sealed with user and notes).
    devices: list[str] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ring": self.ring,
            "kind": self.kind,
            "name": self.name,
            "user": self.user,
            "notes": self.notes,
            "created": self.created,
            "changed": self.changed,
            "public": self.public,
            "devices": self.devices,
        }


class Vault:
    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self._keys: dict[str, bytes] = {}  # open rings' keys: in memory only
        self._used: dict[str, float] = {}  # when each open ring was last used (idle policy)
        self._clock = clock
        self._lock = threading.RLock()
        # The rings' policies (config.yaml `vault:`); the hub sets it from its config.
        self.policy: Callable[[str], Policy] = lambda ring: policy(None, ring)
        self.problems: dict[str, str] = {}  # ring -> why it didn't open by itself, or a warning
        self._held: set[str] = set()  # locked by hand: not opened by itself again until unlocked

    # --- state ---------------------------------------------------------------------------------

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @contextlib.contextmanager
    def unchanged(self) -> Iterator[None]:
        """Nothing in vault.json changes meanwhile (a hub backup reads it)."""
        with self._lock:
            yield

    def ring_ids(self) -> list[str]:
        return list(self._read()["rings"]) if self.exists else []

    def is_open(self, ring: str) -> bool:
        return ring in self._keys

    @property
    def state(self) -> str:
        if not self.exists:
            return "absent"
        try:
            rings = self.ring_ids()
        except Damaged:
            return "damaged"
        open_ = [r for r in rings if r in self._keys]
        return "unlocked" if len(open_) == len(rings) else "partly" if open_ else "locked"

    @property
    def damaged(self) -> str | None:
        """Why vault.json can't be read, or None."""
        try:
            if self.exists:
                self._read()
        except Damaged as e:
            return str(e)
        return None

    def _read(self) -> dict[str, Any]:
        try:
            doc = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            raise Damaged(f"vault.json can't be read: {type(e).__name__}") from e
        if not isinstance(doc, dict) or not isinstance(doc.get("rings"), dict):
            raise Damaged("vault.json isn't a key ring file")
        for ring in doc["rings"].values():
            if not isinstance(ring, dict) or not isinstance(ring.get("slots"), list):
                raise Damaged("vault.json has a ring without slots")
            if not isinstance(ring.get("entries"), dict):
                raise Damaged("vault.json has a ring without entries")
        return doc

    def _write(self, doc: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self.path)
        # The rename itself on disk, before anything relies on it (migration then removes files).
        dir_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _key(self, ring: str) -> bytes:
        key = self._keys.get(ring)
        if key is None:
            raise Locked(ring)
        self._used[ring] = self._clock()
        return key

    def rings(self) -> list[dict[str, Any]]:
        """Each ring: its name, whether it is open, its slots (how it can be opened; never the
        keys), its entry count."""
        if not self.exists:
            return []
        out = []
        for rid, ring in self._read()["rings"].items():
            out.append(
                {
                    "id": rid,
                    "name": ring.get("name") or rid,
                    "open": rid in self._keys,
                    "slots": [
                        {"way": s["way"], "user": s.get("user"), "source": s.get("source", "")}
                        for s in ring["slots"]
                    ],
                    "entries": len(ring["entries"]),
                }
            )
        return out

    # --- slots ---------------------------------------------------------------------------------

    @staticmethod
    def _who(ring: str, slot: dict[str, Any]) -> bytes:
        return _aad(ring, slot["way"], slot.get("user") or slot.get("source") or "")

    def _slot(self, ring: str, way: str, secret: str, key: bytes, **about: str) -> dict[str, Any]:
        salt = os.urandom(16)
        slot: dict[str, Any] = {"way": way, **about, "salt": _b64(salt)}
        if way in AUTO:
            slot["mac"] = _mac(key, secret)
        return slot | _seal(_kek(secret, salt), key, self._who(ring, slot))

    def _try(self, ring: str, slot: dict[str, Any], secret: str) -> bytes | None:
        try:
            return _open(_kek(secret, base64.b64decode(slot["salt"])), slot, self._who(ring, slot))
        except (InvalidTag, KeyError, ValueError):
            return None

    def _same(self, ring: str, slot: dict[str, Any], secret: str, key: bytes) -> bool:
        """Whether a ring-opening slot was made with this key (the ring open, `key` its key)."""
        if "mac" in slot:
            return hmac.compare_digest(slot["mac"], _mac(key, secret))
        return self._try(ring, slot, secret) == key

    def _opened(self, rid: str, key: bytes) -> None:
        """A ring is open: its key in memory; held no more; its SSH keys' public halves checked."""
        self._keys[rid], self._used[rid] = key, self._clock()
        self._held.discard(rid)
        self.problems.pop(rid, None)  # why it didn't open: past
        self._check_publics(rid)

    def _check_publics(self, rid: str) -> None:
        """A public half stored beside the encryption must belong to its private key (it is shown
        in setup commands): one that doesn't is corrected, and noted."""
        import asyncssh  # only here: the vault itself needs no SSH

        doc, fixed = self._read(), []
        ring = doc["rings"].get(rid) or {"entries": {}}
        for id, e in ring["entries"].items():
            if e.get("kind") != "ssh-key":
                continue
            try:
                plain = _open(self._keys[rid], e["secret"], _aad(id, rid, e["kind"])).decode()
                public = asyncssh.import_private_key(plain).export_public_key().decode().strip()
            except (InvalidTag, ValueError, KeyError, asyncssh.KeyImportError):
                continue
            if e.get("public") != public:
                e["public"] = public
                fixed.append(id)
        if fixed:
            self._write(doc)
            self.problems[rid] = f"the public half of {', '.join(fixed)} didn't match its key: corrected"

    def _new_ring(self, doc: dict[str, Any], rid: str, name: str, user: str, password: str, tag: str) -> str:
        """A ring opened by this admin's password and a recovery key (returned, shown once)."""
        if not RING.match(rid):
            raise ValueError("ring ids are lowercase letters, digits, _ and - (up to 40)")
        if rid in doc["rings"]:
            raise ValueError(f"there is a ring {rid!r} already")
        key, recovery = AESGCM.generate_key(256), new_recovery_key()
        doc["rings"][rid] = {
            "name": name,
            "slots": [
                self._slot(rid, "password", password, key, user=user, tag=tag),
                self._slot(rid, "recovery", _normal_recovery(recovery), key),
            ],
            "entries": {},
        }
        self._keys[rid], self._used[rid] = key, self._clock()
        return recovery

    def create(self, user: str, password: str, tag: str) -> dict[str, str]:
        """The two rings (Kastellan's, people's logins), opened by this admin's password; their
        recovery keys (shown once)."""
        with self._lock:
            if self.exists:
                raise ValueError("there is a key ring already")
            doc: dict[str, Any] = {"version": 2, "rings": {}}
            keys = {
                rid: self._new_ring(doc, rid, name, user, password, tag)
                for rid, name in DEFAULT_RINGS.items()
            }
            self._write(doc)
            return keys

    def add_ring(self, rid: str, name: str, user: str, password: str, tag: str) -> str:
        with self._lock:
            doc = self._read()
            recovery = self._new_ring(doc, rid, name, user, password, tag)
            self._write(doc)
            return recovery

    def remove_ring(self, rid: str) -> None:
        with self._lock:
            doc = self._read()
            if rid in DEFAULT_RINGS:
                raise ValueError("Kastellan's two rings stay")
            ring = doc["rings"].get(rid)
            if ring is None:
                raise KeyError(rid)
            if ring["entries"]:
                raise ValueError("move or remove its entries first")
            del doc["rings"][rid]
            self._keys.pop(rid, None)
            self._write(doc)

    def unlock(self, user: str, password: str) -> list[str]:
        """Open every locked ring this admin's password opens (where their policy has it).
        Which ones it opened; WrongKey if this admin has no password slot in any of them (a slot
        made with an older password just doesn't open: `note_stale` says so)."""
        with self._lock:
            opened, slots = [], 0
            for rid, ring in self._read()["rings"].items():
                if ("password", "") not in self.policy(rid).unlock:
                    continue
                for slot in ring["slots"]:
                    if slot["way"] == "password" and slot.get("user") == user:
                        slots += 1
                        if rid not in self._keys and (key := self._try(rid, slot, password)) is not None:
                            self._opened(rid, key)
                            opened.append(rid)
            if not slots:
                raise WrongKey("that password doesn't open a key ring")
            return opened

    def note_stale(self, user: str, tag: str) -> list[str]:
        """Rings still locked whose slot for this admin was made with another password (theirs
        changed): noted in `problems`. Which rings."""
        stale = []
        with self._lock:
            for rid, ring in self._read()["rings"].items():
                mine = [s for s in ring["slots"] if s["way"] == "password" and s.get("user") == user]
                if rid not in self._keys and mine and all(s.get("tag") != tag for s in mine):
                    self.problems[rid] = (
                        f"{user}'s password changed since this ring was set up for it: open it with its "
                        "recovery key, or let another admin sign in"
                    )
                    stale.append(rid)
        return stale

    def has_slot(self, rid: str, user: str, tag: str) -> bool:
        """Whether this admin has a password slot of their own in this ring, made with the
        password they have now (`tag`: auth.password_tag of its hash). Using a login there needs
        one: an admin without it doesn't use the ring's logins only because someone opened it."""
        if not self.exists:
            return False
        with self._lock:
            ring = self._read()["rings"].get(rid)
            return any(
                s["way"] == "password" and s.get("user") == user and s.get("tag") == tag
                for s in (ring["slots"] if ring else [])
            )

    def opens_with(self, rid: str, user: str, password: str) -> bool:
        """Whether this admin's own password slot opens this ring (a second check before showing
        one of its secrets)."""
        with self._lock:
            ring = self._read()["rings"].get(rid)
            return any(
                s["way"] == "password" and s.get("user") == user and self._try(rid, s, password) is not None
                for s in (ring["slots"] if ring else [])
            )

    def recovery_tag(self, rid: str) -> str:
        """An opaque identifier for the current recovery slots, to expire old check results."""
        with self._lock:
            ring = self._read()["rings"].get(rid) or {}
            slots = [s for s in ring.get("slots", []) if s["way"] == "recovery"]
            return hashlib.sha256(json.dumps(slots, sort_keys=True).encode()).hexdigest()

    def check_recovery(self, rid: str, recovery: str) -> bool:
        """Whether this recovery key opens the ring (without opening it)."""
        with self._lock:
            ring = self._read()["rings"].get(rid)
            return any(
                slot["way"] == "recovery" and self._try(rid, slot, _normal_recovery(recovery)) is not None
                for slot in (ring["slots"] if ring else [])
            )

    def unlock_recovery(self, rid: str, recovery: str) -> None:
        with self._lock:
            ring = self._read()["rings"].get(rid)
            for slot in ring["slots"] if ring else []:
                if slot["way"] == "recovery" and (key := self._try(rid, slot, _normal_recovery(recovery))):
                    self._opened(rid, key)
                    return
        raise WrongKey("that recovery key doesn't open this ring")

    def unlock_by_itself(self, environ: dict[str, str], config_keys: dict[str, str]) -> list[str]:
        """At start (and after a config change): open the rings whose policy names a key file,
        an environment variable or a key in config.yaml, with that key; not a ring locked by
        hand since. What it opened; why others didn't is in `problems`."""
        opened = []
        with self._lock:
            if not self.exists:
                return []
            for rid, ring in self._read()["rings"].items():
                auto = [w for w in self.policy(rid).unlock if w[0] in AUTO]
                if rid in self._keys or rid in self._held or not auto:
                    continue
                self.problems.pop(rid, None)
                notes = []
                for way, source in auto:
                    secret, note = self._source(rid, way, source, environ, config_keys)
                    if note:
                        notes.append(note)
                    slot = next(
                        (s for s in ring["slots"] if s["way"] == way and s.get("source", "") == source), None
                    )
                    if secret is None:
                        continue
                    if slot is None:
                        notes.append(f"{_describe(way, source)} is not set up as a way to open it")
                        continue
                    key = self._try(rid, slot, secret)
                    if key is None:
                        notes.append(f"{_describe(way, source)} doesn't open it")
                        continue
                    self._opened(rid, key)
                    opened.append(rid)
                    break
                if notes:  # why it didn't open, or a warning (a key file others may read)
                    self.problems[rid] = "; ".join(notes)
        return opened

    @staticmethod
    def _source(
        rid: str, way: str, source: str, environ: dict[str, str], config_keys: dict[str, str]
    ) -> tuple[str | None, str | None]:
        """A ring key from where its way says, and a note (missing, readable by others)."""
        if way == "env":
            value = environ.get(source)
            return (value, None) if value else (None, f"the environment variable {source} isn't set")
        if way == "config":
            value = config_keys.get(rid)
            return (value, None) if value else (None, "config.yaml has no key for it")
        try:
            return read_keyfile(source)
        except OSError as e:
            return None, f"can't read {source}: {e.strerror or type(e).__name__}"

    def lock(self, ring: str | None = None, by_hand: bool = True) -> list[str]:
        """Forget one ring's key (or every ring's). Which were open. Locked by hand, a ring stays
        locked through config reloads until it is unlocked (or the hub restarts)."""
        with self._lock:
            gone = [r for r in self._keys if ring is None or r == ring]
            for r in gone:
                del self._keys[r]
            if by_hand:
                self._held |= set(self.ring_ids()) if ring is None else {ring}
            return gone

    def relock(self, admins_signed_in: bool) -> list[str]:
        """Close what its policy says is due now: rings that stay open only while an admin is
        using the app, and rings unused for their idle time."""
        due = []
        now = self._clock()
        with self._lock:
            for rid in list(self._keys):
                p = self.policy(rid)
                if (p.stays == "signed_in" and not admins_signed_in) or (
                    p.idle is not None and now - self._used.get(rid, now) > p.idle
                ):
                    del self._keys[rid]
                    due.append(rid)
        return due

    def keep_slot(self, user: str, password: str, tag: str, admins: set[str]) -> bool:
        """For every OPEN ring whose policy opens with a password: this admin's slot opens with
        their password now (made, or made again after a password change); slots of people who
        are no longer admins go, and so do password slots of rings that no longer open that way.
        A locked ring is never touched (its slots can't be made again without its key), and a
        ring always keeps a way to open besides its recovery key. Whether it changed the file."""
        with self._lock:
            doc, changed = self._read(), False
            for rid, ring in doc["rings"].items():
                if rid not in self._keys:
                    continue
                key = self._keys[rid]
                by_password = ("password", "") in self.policy(rid).unlock
                keep = [
                    s
                    for s in ring["slots"]
                    if s["way"] != "password" or (by_password and s.get("user") in admins)
                ]
                if by_password and user in admins:
                    mine = next((s for s in keep if s["way"] == "password" and s.get("user") == user), None)
                    # The tag says the slot was made with the password the config holds now.
                    if mine is None or mine.get("tag") != tag:
                        keep = [s for s in keep if not (s["way"] == "password" and s.get("user") == user)]
                        keep.append(self._slot(rid, "password", password, key, user=user, tag=tag))
                if not any(s["way"] != "recovery" for s in keep):
                    keep = [s for s in ring["slots"]]  # never down to the recovery key alone
                if keep != ring["slots"]:
                    ring["slots"], changed = keep, True
            if changed:
                self._write(doc)
            return changed

    def set_ways(self, rid: str, ways: dict[Way, str], unread: frozenset[Way] = frozenset()) -> bool:
        """While the ring is open: the slots that open it by itself are exactly `ways` (each
        with its key), plus those of `unread` ways (chosen, but their key can't be read right
        now: their slot stays as it is); password and recovery slots stay as they are. Never
        leaves only the recovery key. Whether it changed the file."""
        with self._lock:
            key = self._key(rid)
            doc = self._read()
            ring = doc["rings"][rid]
            kept = [
                s for s in ring["slots"] if s["way"] not in AUTO or (s["way"], s.get("source", "")) in unread
            ]
            for (way, source), secret in ways.items():
                old = next(
                    (s for s in ring["slots"] if s["way"] == way and s.get("source", "") == source), None
                )
                if old is not None and self._same(rid, old, secret, key):
                    kept.append(old)
                else:
                    kept.append(self._slot(rid, way, secret, key, source=source))
            if not any(s["way"] != "recovery" for s in kept):
                raise ValueError("that would leave only its recovery key to open it")
            if kept == ring["slots"]:
                return False
            ring["slots"] = kept
            self._write(doc)
            return True

    def new_recovery(self, rid: str) -> str:
        """A new recovery key for a ring (the old one stops working)."""
        with self._lock:
            key = self._key(rid)
            doc, recovery = self._read(), new_recovery_key()
            ring = doc["rings"][rid]
            ring["slots"] = [s for s in ring["slots"] if s["way"] != "recovery"] + [
                self._slot(rid, "recovery", _normal_recovery(recovery), key)
            ]
            self._write(doc)
            return recovery

    # --- entries -------------------------------------------------------------------------------

    def _find(self, doc: dict[str, Any], id: str) -> tuple[str, dict[str, Any]] | None:
        for rid, ring in doc["rings"].items():
            if id in ring["entries"]:
                return rid, ring["entries"][id]
        return None

    def _about(self, rid: str, id: str, e: dict[str, Any]) -> tuple[str, str, list[str]]:
        """A login's user name, notes and devices: sealed with the ring's key (so they can't be
        changed in the file unnoticed); empty while it is locked."""
        key = self._keys.get(rid)
        if key is None or "about" not in e:
            return "", "", []
        try:
            about = json.loads(_open(key, e["about"], _aad(id, rid, e["kind"], "about")))
        except (InvalidTag, ValueError):
            return "", "", []
        devices = about.get("devices")
        devices = [str(d) for d in devices] if isinstance(devices, list) else []
        return str(about.get("user", "")), str(about.get("notes", "")), devices

    def entries(self) -> list[Entry]:
        if not self.exists:
            return []
        out = []
        for rid, ring in self._read()["rings"].items():
            for id, e in ring["entries"].items():
                user, notes, devices = self._about(rid, id, e)
                out.append(
                    Entry(
                        id,
                        rid,
                        e["kind"],
                        e.get("name", ""),
                        user,
                        notes,
                        e.get("created", 0),
                        e.get("changed", 0),
                        e.get("public", ""),
                        devices,
                    )
                )
        return sorted(out, key=lambda e: (e.ring, e.name.lower() or e.id))

    def entry(self, id: str) -> Entry | None:
        return next((e for e in self.entries() if e.id == id), None)

    def has(self, id: str) -> bool:
        return self.exists and self._find(self._read(), id) is not None

    def get(self, id: str, by: str | None = None) -> str:
        """An entry's secret. `by`: the signed-in admin whose own action needs it; None for
        Kastellan's own (background) use, which only rings whose policy allows it may serve
        (NotForKastellan otherwise). KeyError when there is no such entry, Locked while its ring
        is."""
        with self._lock:
            found = self._find(self._read(), id)
            if found is None:
                raise KeyError(id)
            rid, e = found
            if by is None and not self.policy(rid).background:
                raise NotForKastellan(f"{id} is in the ring {rid!r}: Kastellan never uses it by itself")
            key = self._key(rid)
            try:
                return _open(key, e["secret"], _aad(id, rid, e["kind"])).decode()
            except InvalidTag as err:
                raise ValueError(f"{id} doesn't decrypt (changed by hand?)") from err

    def put(
        self,
        id: str,
        secret: str | None,
        *,
        ring: str | None = None,
        kind: Kind = "password",
        name: str = "",
        user: str = "",
        notes: str = "",
        public: str = "",
        devices: list[str] | None = None,
    ) -> None:
        """Add or change an entry (a new one goes into `ring`, Kastellan's by default; an
        existing one stays where it is: `move` moves it). `secret` None keeps the one it has."""
        if not ID.match(id):
            raise ValueError("ids are lowercase letters, digits, . _ / - (up to 128)")
        with self._lock:
            doc = self._read()
            found = self._find(doc, id)
            rid = found[0] if found else ring or KASTELLAN
            if rid not in doc["rings"]:
                raise KeyError(rid)
            key = self._key(rid)
            old = found[1] if found else None
            if secret is None:
                if old is None:
                    raise KeyError(id)
                if old["kind"] != kind:  # re-sealed: the kind is bound in
                    secret = _open(key, old["secret"], _aad(id, rid, old["kind"])).decode()
            now = time.time()
            if devices is None:  # kept as they were
                devices = self._about(rid, id, old)[2] if old else []
            about = json.dumps({"user": user, "notes": notes, "devices": sorted(set(devices))}).encode()
            doc["rings"][rid]["entries"][id] = {
                "kind": kind,
                "name": name,
                "public": public,
                "created": old["created"] if old else now,
                "changed": now if secret is not None or not old else old["changed"],
                "secret": _seal(key, secret.encode(), _aad(id, rid, kind))
                if secret is not None
                else old["secret"],
                "about": _seal(key, about, _aad(id, rid, kind, "about")),
            }
            self._write(doc)

    def move(self, id: str, to: str) -> None:
        """An entry into another ring (both open): sealed again with that ring's key."""
        with self._lock:
            doc = self._read()
            found = self._find(doc, id)
            if found is None:
                raise KeyError(id)
            rid, e = found
            if to not in doc["rings"]:
                raise KeyError(to)
            if to == rid:
                return
            plain = _open(self._key(rid), e["secret"], _aad(id, rid, e["kind"]))
            target = self._key(to)
            moved = e | {"secret": _seal(target, plain, _aad(id, to, e["kind"]))}
            if "about" in e:
                about = _open(self._keys[rid], e["about"], _aad(id, rid, e["kind"], "about"))
                moved["about"] = _seal(target, about, _aad(id, to, e["kind"], "about"))
            doc["rings"][to]["entries"][id] = moved
            del doc["rings"][rid]["entries"][id]
            self._write(doc)

    def delete(self, id: str) -> None:
        with self._lock:
            doc = self._read()
            found = self._find(doc, id)
            if found is None:
                raise KeyError(id)
            self._key(found[0])  # only while its ring is open: deleting is a change like any other
            del doc["rings"][found[0]]["entries"][id]
            self._write(doc)


def _describe(way: str, source: str) -> str:
    return {"keyfile": f"the key file {source}", "env": f"${source}", "config": "the key in config.yaml"}.get(
        way, way
    )


def shred(path: Path) -> None:
    """Overwrite a file once, then remove it (a plain copy must not stay readable on disk). A
    symlink is only removed: what it points to may be someone's own file."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(info.st_mode):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "wb") as f:  # no O_TRUNC: the same blocks are overwritten
                f.write(b"\0" * info.st_size)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass
    path.unlink(missing_ok=True)
