"""Web push: alarms on the phones and computers people allowed it on (RFC 8030, 8291, 8292).

The hub's VAPID key is `<data>/push/vapid.pem` (0600, made once); the browser subscriptions are
`<data>/push/subscriptions.json`, each with its user. A `push:` notify channel names a user (or
`*` for everyone) and has `min` and `quiet` like the others. Browsers allow push only on secure
pages: the hub must be reached over HTTPS (a reverse proxy) or as localhost.

The push services (Google's, Mozilla's, Apple's) see when a message goes, never what it says:
the payload is encrypted for the browser. It holds a title, a line and the device's page.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import http_ece
import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid02

from .vault import Vault

log = logging.getLogger(__name__)

SUBJECT = "https://andiwand.github.io"  # who sends (VAPID `sub`): the software's site, no person
TTL = 24 * 3600  # a phone that is off gets it within a day, or not at all
PER_USER = 10  # browsers per person; a new one beyond that replaces the oldest
POST_LIMIT = 15  # seconds for one browser's push
RETRY_EVERY = 60  # a failed push is tried again after 1, 2, 4 ... 64 minutes, for a day
MAX_PENDING = 1000
# Permanent answers from a push service: the subscription is gone or not ours (e.g. made for an
# older VAPID key). Anything else (429, 5xx, no answer) is tried again later.
# Gone for good: only what the push service says of the subscription itself. 400/401/403 can be
# the hub's own fault (a clock far off makes its signature invalid): retried, never dropped.
GONE = {404, 410}
# The browsers' push services. Only they are posted to: a subscription is data from a signed-in
# person, and a name of their choosing could lead the hub's POSTs to a service at home.
SERVICES = ("fcm.googleapis.com", ".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def valid_endpoint(endpoint: str) -> bool:
    """A known push service's address, over https on its usual port."""
    url = urlsplit(endpoint)
    host = url.hostname or ""
    if url.scheme != "https" or url.port not in (None, 443) or len(endpoint) > 1000:
        return False
    return any(host == s or (s.startswith(".") and host.endswith(s)) for s in SERVICES)


VAULT_ID = "push/vapid"  # its key in the key ring, once there is one


class Push:
    def __init__(self, data: Path, vault: Vault | None = None) -> None:
        self.folder = data / "push"
        self.vault = vault
        self.path = self.folder / "subscriptions.json"
        self._lock = threading.Lock()
        self._vapid: Vapid02 | None = None
        # (endpoint, payload, first try, last try, tries): failed pushes, per browser.
        self._pending: list[tuple[str, dict[str, Any], float, float, int]] = []
        try:
            self.subscriptions: list[dict[str, Any]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.subscriptions = []

    def forget_key(self) -> None:
        """The key ring was locked: its key leaves memory too."""
        with self._lock:
            self._vapid = None

    def vapid(self) -> Vapid02:
        """The hub's own key for push (made on first use). vault.Locked while the key ring is."""
        with self._lock:
            if self._vapid is None and self.vault is not None and self.vault.exists:
                if self.vault.has(VAULT_ID):
                    self._vapid = Vapid02.from_pem(self.vault.get(VAULT_ID).encode())
                else:
                    key = Vapid02()
                    key.generate_keys()
                    self.vault.put(VAULT_ID, key.private_pem().decode(), kind="other", name="Push key")
                    self._vapid = key
            if self._vapid is None:
                pem = self.folder / "vapid.pem"
                if pem.exists():
                    self._vapid = Vapid02.from_file(str(pem))
                else:
                    key = Vapid02()
                    key.generate_keys()
                    self.folder.mkdir(parents=True, exist_ok=True)
                    fd = os.open(pem, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(key.private_pem())
                    self._vapid = key
            return self._vapid

    @property
    def public_key(self) -> str:
        """For the browser's subscribe (applicationServerKey)."""
        return _b64(self.vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))

    def _save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.subscriptions, f, indent=1)
        tmp.replace(self.path)

    def add(self, user: str, endpoint: str, p256dh: str, auth: str, label: str) -> None:
        if not valid_endpoint(endpoint):
            raise ValueError("not a push service Kastellan knows (Chrome, Firefox, Safari, Edge)")
        if len(_unb64(p256dh)) != 65 or len(_unb64(auth)) != 16:
            raise ValueError("not a browser's push keys")
        with self._lock:
            self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]
            mine = [s for s in self.subscriptions if s["user"] == user]
            for old in sorted(mine, key=lambda s: s.get("created", 0))[: max(0, len(mine) - PER_USER + 1)]:
                self.subscriptions.remove(old)
            self.subscriptions.append(
                {
                    "user": user,
                    "endpoint": endpoint,
                    "p256dh": p256dh,
                    "auth": auth,
                    "label": label[:80],
                    "created": int(time.time()),
                }
            )
            self._save()

    def remove(self, user: str, endpoint: str) -> bool:
        with self._lock:
            kept = [s for s in self.subscriptions if not (s["endpoint"] == endpoint and s["user"] == user)]
            if len(kept) == len(self.subscriptions):
                return False
            self.subscriptions = kept
            self._save()
            return True

    def of(self, user: str) -> list[dict[str, Any]]:
        return [s for s in self.subscriptions if s["user"] == user]

    async def send(
        self, client: httpx.AsyncClient, user: str, payload: dict[str, Any], retry: bool = True
    ) -> bool:
        """To every browser of `user` (`*`: everyone's), at once. True when all took it; a browser
        that couldn't be reached is tried again later on its own (`retry`, by `retry_due`), so
        the others don't get it twice. A browser that is gone for good is dropped."""
        targets = list(self.subscriptions) if user == "*" else self.of(user)
        results = await asyncio.gather(*(self._post(client, s, payload) for s in targets))
        if retry:
            now = time.time()
            for s, ok in zip(targets, results, strict=True):
                if not ok:
                    self._pending.append((s["endpoint"], payload, now, now, 1))
            del self._pending[:-MAX_PENDING]
        return all(results)

    async def retry_due(self, client: httpx.AsyncClient, blocked_urls: set[str] | None = None) -> None:
        """Try the failed pushes that are due again (after 1, 2, 4 ... minutes, for a day)."""
        now, due, later = time.time(), [], []
        known = {s["endpoint"]: s for s in self.subscriptions}
        for endpoint, payload, first, last, tries in self._pending:
            if endpoint not in known or now - first > TTL or payload.get("url") in (blocked_urls or set()):
                continue  # dropped meanwhile, or too old to matter
            if now - last >= RETRY_EVERY * 2 ** min(tries - 1, 6):
                due.append((endpoint, payload, first, tries))
            else:
                later.append((endpoint, payload, first, last, tries))
        self._pending = later
        try:
            results = await asyncio.gather(*(self._post(client, known[e], p) for e, p, _, _ in due))
        except asyncio.CancelledError:  # the hub's time limit: they stay due, nothing is lost
            self._pending += [(e, p, first, 0.0, tries) for e, p, first, tries in due]
            raise
        for (endpoint, payload, first, tries), ok in zip(due, results, strict=True):
            if not ok:
                self._pending.append((endpoint, payload, first, now, tries + 1))

    async def _post(self, client: httpx.AsyncClient, s: dict[str, Any], payload: dict[str, Any]) -> bool:
        """One browser's push; never raises. False: try again later (not when it is gone)."""
        host = urlsplit(s["endpoint"]).hostname
        if not valid_endpoint(s["endpoint"]):  # stored before only push services were allowed
            self._drop(s["endpoint"])
            return True
        try:
            r = await asyncio.wait_for(
                client.post(s["endpoint"], content=self._encrypt(s, payload), headers=self._headers(s)),
                POST_LIMIT,
            )
        except Exception as e:  # the endpoint names the browser: only its service is logged
            log.warning("push to %s failed: %s", host, type(e).__name__)
            return False
        if r.status_code in GONE:
            log.info("push to %s: %s, the browser's subscription is dropped", host, r.status_code)
            self._drop(s["endpoint"])
            return True
        if r.status_code in (400, 401, 403):
            log.warning("push to %s refused: HTTP %s (is the hub's clock right?)", host, r.status_code)
        elif not r.is_success:
            log.warning("push to %s failed: HTTP %s", host, r.status_code)
        return r.is_success

    def _drop(self, endpoint: str) -> None:
        with self._lock:
            self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]
            self._save()

    def _encrypt(self, s: dict[str, Any], payload: dict[str, Any]) -> bytes:
        return http_ece.encrypt(
            json.dumps(payload).encode(),
            salt=os.urandom(16),
            private_key=ec.generate_private_key(ec.SECP256R1()),
            dh=_unb64(s["p256dh"]),
            auth_secret=_unb64(s["auth"]),
            version="aes128gcm",
        )

    def _headers(self, s: dict[str, Any]) -> dict[str, str]:
        url = urlsplit(s["endpoint"])
        claims = {"aud": f"{url.scheme}://{url.netloc}", "sub": SUBJECT, "exp": int(time.time()) + 12 * 3600}
        return {
            **self.vapid().sign(claims),
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "TTL": str(TTL),
        }
