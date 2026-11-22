"""Sign-in: users and roles from config.yaml, password hashes, sessions, and a brake on guessing.

    users:
      andreas:
        role: admin          # admin: everything; observer: sees everything, changes nothing
        password: scrypt$... # from `kastellan hash-password`

Sign-in is off while `users` is empty (like Norn): everyone is an admin. Features that hand out
shells or screens (terminal, remote desktop) refuse to work then; see `require_signin`.

Sessions are random tokens in an HttpOnly, SameSite=Strict cookie. The hub keeps only their
SHA-256 in <data>/sessions.json, so the file is no use to someone who reads it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SESSION_COOKIE = "kastellan_session"
SESSION_IDLE = 30 * 86400  # a session ends after 30 days without use
TOUCH_EVERY = 3600  # how often last use is written back to the file

# scrypt: N=2^15, r=8, p=1 (about 32 MB and ~50 ms per hash).
_N, _R, _P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, maxmem=64 * 1024 * 1024, dklen=32)
    b64 = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${b64(salt).decode()}${b64(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(key)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Checked against when the user does not exist, so a wrong name takes as long as a wrong password.
DUMMY_HASH = hash_password(secrets.token_hex(8))


def password_tag(stored_hash: str) -> str:
    """A short fingerprint of a user's password hash. A session remembers it, so changing the
    password in config.yaml ends every session made with the old one."""
    return hashlib.sha256(stored_hash.encode()).hexdigest()[:16]


@dataclass
class Session:
    user: str
    created: float
    used: float
    tag: str = ""  # password_tag at sign-in; sessions from before tags existed match nothing


class Sessions:
    """Sessions in <data>/sessions.json, keyed by the SHA-256 of their token."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        # When each user last used the app themselves (not a page polling on its own): in memory.
        self._active: dict[str, float] = {}
        try:
            data = json.loads(path.read_text())
            self._sessions = {k: Session(**v) for k, v in data.items()}
        except (OSError, ValueError, TypeError):
            pass

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: vars(s) for k, s in self._sessions.items()}))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def create(self, user: str, tag: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            # Sessions whose cookie never came back (a cleared browser) are dropped here.
            self._sessions = {k: s for k, s in self._sessions.items() if now - s.used <= SESSION_IDLE}
            self._sessions[self._key(token)] = Session(user, now, now, tag)
            self._save()
        self.mark(user)
        return token

    def mark(self, user: str) -> None:
        """This user is using the app now (a request they made, not an automatic refresh)."""
        self._active[user] = time.time()

    def active(self, within: float) -> set[str]:
        """Who used the app within the last `within` seconds and still has a session."""
        now = time.time()
        live = self.users()
        return {u for u, t in self._active.items() if now - t <= within and u in live}

    def tag(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(self._key(token))
            return session.tag if session else None

    def end_user(self, user: str) -> None:
        """Sign a user out everywhere."""
        with self._lock:
            gone = [k for k, s in self._sessions.items() if s.user == user]
            for k in gone:
                del self._sessions[k]
            if gone:
                self._save()

    def user(self, token: str | None, now: float | None = None) -> str | None:
        """The user of a live session, or None."""
        if not token:
            return None
        now = time.time() if now is None else now
        key = self._key(token)
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return None
            if now - session.used > SESSION_IDLE:
                del self._sessions[key]
                self._save()
                return None
            if now - session.used > TOUCH_EVERY:
                session.used = now
                self._save()
            return session.user

    def end(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._sessions.pop(self._key(token), None) is not None:
                self._save()

    def users(self) -> set[str]:
        """Who has a session that hasn't idled out."""
        now = time.time()
        with self._lock:
            return {s.user for s in self._sessions.values() if now - s.used <= SESSION_IDLE}

    def end_all_except(self, users: set[str]) -> None:
        """Drop the sessions of users who are no longer in config.yaml."""
        with self._lock:
            gone = [k for k, s in self._sessions.items() if s.user not in users]
            for k in gone:
                del self._sessions[k]
            if gone:
                self._save()


class Brake:
    """Slows down password guessing: after `limit` attempts from one address within `window`
    seconds, that address must wait until the window has passed. Attempts are counted before the
    password is checked (see auth_api); a success takes back only its own attempt, so signing in
    to one account doesn't wipe the guesses at another."""

    def __init__(self, limit: int = 5, window: float = 300) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}
        self._noted: dict[str, float] = {}  # when an address's block was last written to the audit log

    def _recent(self, address: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(address, []) if now - t < self.window]
        if recent:
            self._failures[address] = recent
        else:
            self._failures.pop(address, None)
        return recent

    def blocked(self, address: str, now: float | None = None) -> float:
        """Seconds to wait, or 0."""
        now = time.time() if now is None else now
        recent = self._recent(address, now)
        if len(recent) < self.limit:
            return 0
        return self.window - (now - recent[0])

    def first_block(self, address: str, now: float | None = None) -> bool:
        """True once per window while an address is blocked: for the audit log, which must not
        get a line per rejected attempt."""
        now = time.time() if now is None else now
        if now - self._noted.get(address, -1e18) < self.window:
            return False
        self._noted[address] = now
        return True

    def failed(self, address: str, now: float | None = None) -> float:
        """Counts an attempt; returns its time, for `succeeded`."""
        now = time.time() if now is None else now
        self._failures.setdefault(address, []).append(now)
        if len(self._failures) > 10_000:  # many addresses: forget the ones done waiting
            for a in list(self._failures):
                self._recent(a, now)
            self._noted = {a: t for a, t in self._noted.items() if now - t < self.window}
        return now

    def succeeded(self, address: str, attempt: float) -> None:
        """The attempt counted at `attempt` was right: it no longer counts."""
        times = self._failures.get(address, [])
        if attempt in times:
            times.remove(attempt)
