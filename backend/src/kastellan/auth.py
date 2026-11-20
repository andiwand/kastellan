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
_DUMMY_HASH = hash_password(secrets.token_hex(8))


@dataclass
class Session:
    user: str
    created: float
    used: float


class Sessions:
    """Sessions in <data>/sessions.json, keyed by the SHA-256 of their token."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
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

    def create(self, user: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[self._key(token)] = Session(user, now, now)
            self._save()
        return token

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

    def end_all_except(self, users: set[str]) -> None:
        """Drop the sessions of users who are no longer in config.yaml."""
        with self._lock:
            gone = [k for k, s in self._sessions.items() if s.user not in users]
            for k in gone:
                del self._sessions[k]
            if gone:
                self._save()


class Brake:
    """Slows down password guessing: after `limit` failures from one address within `window`
    seconds, that address must wait until the window has passed."""

    def __init__(self, limit: int = 5, window: float = 300) -> None:
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}

    def blocked(self, address: str, now: float | None = None) -> float:
        """Seconds to wait, or 0."""
        now = time.time() if now is None else now
        recent = [t for t in self._failures.get(address, []) if now - t < self.window]
        self._failures[address] = recent
        if len(recent) < self.limit:
            return 0
        return self.window - (now - recent[0])

    def failed(self, address: str, now: float | None = None) -> None:
        self._failures.setdefault(address, []).append(time.time() if now is None else now)

    def succeeded(self, address: str) -> None:
        self._failures.pop(address, None)
