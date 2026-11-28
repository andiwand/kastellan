"""WireGuard clients through wg-easy's API (v15): list, create, enable, disable, delete, and the
client's config file and QR code. wg-easy keeps doing what it does well (keys, the server
config, the interface); Kastellan is the place where access is given and taken away.

wg-easy authenticates its API with HTTP Basic (its admin login; 2FA must be off for the API).
Routes as in wg-easy v15.5's src/server/api/client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

MAX_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WgClient:
    id: str
    name: str
    enabled: bool
    address: str | None
    created: str | None
    expires: str | None
    last_handshake: str | None
    rx: int | None
    tx: int | None


def _client(item: dict[str, Any]) -> WgClient:
    def text(key: str) -> str | None:
        value = item.get(key)
        return str(value)[:100] if value not in (None, "") else None

    def number(key: str) -> int | None:
        value = item.get(key)
        return int(value) if isinstance(value, int | float) else None

    return WgClient(
        id=str(item.get("id", item.get("clientId", "")))[:40],
        name=str(item.get("name") or "?")[:100],
        enabled=item.get("enabled") is not False,
        address=text("ipv4Address"),
        created=text("createdAt"),
        expires=text("expiresAt"),
        last_handshake=text("latestHandshakeAt"),
        rx=number("transferRx"),
        tx=number("transferTx"),
    )


class Rates:
    """Transfer rates (bytes per second) from wg-easy's running totals, between two reads of
    them. A read soon after the last one keeps the last rate (two open pages would measure a
    second or two); after a long gap, or a total that went back (the interface restarted),
    there is no rate until the next read."""

    MIN_GAP = 5.0
    MAX_GAP = 600.0

    def __init__(self) -> None:
        self._last: dict[str, tuple[float, int, int]] = {}
        self._rate: dict[str, tuple[float, float] | None] = {}

    def update(self, key: str, rx: int | None, tx: int | None, now: float) -> tuple[float, float] | None:
        """(rx, tx) per second for this client: what it sent the server and what it got."""
        if rx is None or tx is None:
            return None
        last = self._last.get(key)
        if last is not None and 0 <= now - last[0] < self.MIN_GAP:
            return self._rate.get(key)
        self._last[key] = (now, rx, tx)
        rate = None
        if last is not None and rx >= last[1] and tx >= last[2] and now - last[0] <= self.MAX_GAP:
            gap = now - last[0]
            rate = ((rx - last[1]) / gap, (tx - last[2]) / gap)
        self._rate[key] = rate
        return rate


class WgEasy:
    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.auth = httpx.BasicAuth(user, password)

    async def _call(self, method: str, path: str, **kwargs: Any) -> bytes:
        """The answer's body, read only up to MAX_BYTES."""
        body = bytearray()
        async with (
            httpx.AsyncClient(auth=self.auth, timeout=15) as client,
            client.stream(method, f"{self.url}/api{path}", **kwargs) as r,
        ):
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ValueError("answer too large")
        return bytes(body)

    async def clients(self) -> list[WgClient]:
        data = json.loads(await self._call("GET", "/client"))
        if not isinstance(data, list):
            raise ValueError("unexpected answer from wg-easy")
        return [_client(i) for i in data if isinstance(i, dict)]

    async def create(self, name: str, expires: datetime | None) -> str:
        body = {"name": name, "expiresAt": expires.isoformat() if expires else None}
        data = json.loads(await self._call("POST", "/client", json=body))
        return str(data.get("clientId"))

    async def enable(self, id: str, on: bool) -> None:
        await self._call("POST", f"/client/{_id(id)}/{'enable' if on else 'disable'}")

    async def delete(self, id: str) -> None:
        await self._call("DELETE", f"/client/{_id(id)}")

    async def configuration(self, id: str) -> str:
        return (await self._call("GET", f"/client/{_id(id)}/configuration")).decode("utf-8", "replace")

    async def qrcode(self, id: str) -> str:
        return (await self._call("GET", f"/client/{_id(id)}/qrcode.svg")).decode("utf-8", "replace")


def _id(id: str) -> str:
    if not id.isdigit():  # wg-easy's ids are numbers; nothing else goes into a URL path
        raise ValueError("bad client id")
    return id
