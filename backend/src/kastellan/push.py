"""Web push: alarms on the phones and computers people allowed it on (RFC 8030, 8291, 8292).

The hub's VAPID key is `<data>/push/vapid.pem` (0600, made once); the browser subscriptions are
`<data>/push/subscriptions.json`, each with its user. A `push:` notify channel names a user (or
`*` for everyone) and has `min` and `quiet` like the others. Browsers allow push only on secure
pages: the hub must be reached over HTTPS (a reverse proxy) or as localhost.

The push services (Google's, Mozilla's, Apple's) see when a message goes, never what it says:
the payload is encrypted for the browser. It holds a title, a line and the device's page.
"""

from __future__ import annotations

import base64
import ipaddress
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

log = logging.getLogger(__name__)

SUBJECT = "https://andiwand.github.io"  # who sends (VAPID `sub`): the software's site, no person
TTL = 24 * 3600  # a phone that is off gets it within a day, or not at all


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def valid_endpoint(endpoint: str) -> bool:
    """A push service's address: https, by name (never an address at home: the hub would POST
    wherever a subscription points)."""
    url = urlsplit(endpoint)
    if url.scheme != "https" or not url.hostname or len(endpoint) > 1000:
        return False
    try:
        ipaddress.ip_address(url.hostname)
        return False
    except ValueError:
        return "." in url.hostname and not url.hostname.endswith((".lan", ".local", ".home.arpa"))


class Push:
    def __init__(self, data: Path) -> None:
        self.folder = data / "push"
        self.path = self.folder / "subscriptions.json"
        self._lock = threading.Lock()
        self._vapid: Vapid02 | None = None
        try:
            self.subscriptions: list[dict[str, Any]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.subscriptions = []

    def vapid(self) -> Vapid02:
        """The hub's own key for push (made on first use)."""
        with self._lock:
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
            raise ValueError("not a push service's address")
        if len(_unb64(p256dh)) != 65 or len(_unb64(auth)) != 16:
            raise ValueError("not a browser's push keys")
        with self._lock:
            self.subscriptions = [s for s in self.subscriptions if s["endpoint"] != endpoint]
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

    async def send(self, client: httpx.AsyncClient, user: str, payload: dict[str, Any]) -> bool:
        """To every browser of `user` (`*`: everyone's). False when one could not be reached now
        (to try again later); a browser that left (404, 410) is dropped."""
        targets = list(self.subscriptions) if user == "*" else self.of(user)
        ok, gone = True, []
        for s in targets:
            try:
                r = await client.post(
                    s["endpoint"], content=self._encrypt(s, payload), headers=self._headers(s)
                )
                if r.status_code in (404, 410):
                    gone.append(s["endpoint"])
                else:
                    r.raise_for_status()
            except Exception as e:  # never raised into the hub; the endpoint names the browser
                log.warning("push to %s failed: %s", urlsplit(s["endpoint"]).hostname, type(e).__name__)
                ok = False
        if gone:
            with self._lock:
                self.subscriptions = [s for s in self.subscriptions if s["endpoint"] not in gone]
                self._save()
        return ok

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
