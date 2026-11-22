"""Pinned TLS connections reused between device requests; h11 handles HTTP framing. Also what
the device clients (OpenWrt, FRITZ!Box) share: the pinned fingerprints and their one change at
a time.

A request is sent again after a reused connection closes without a response only when it is
idempotent (by default GET and HEAD; callers say so for their reads). A device may have done a
write before disconnecting, so replaying it could repeat an action.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ssl
from collections.abc import Callable
from pathlib import Path

import h11

IDLE_FOR = 8.0
MAX_IDLE = 2  # per port


class ChangedCertificate(Exception):
    pass


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


class Pins:
    """Each device's pinned certificate fingerprint: <folder>/<id>.sha256."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def get(self, id: str) -> str | None:
        try:
            return (self.folder / f"{id}.sha256").read_text().strip() or None
        except OSError:
            return None

    def set(self, id: str, fp: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{id}.sha256").write_text(fp + "\n")

    def forget(self, id: str) -> None:
        (self.folder / f"{id}.sha256").unlink(missing_ok=True)


class Changing:
    """A device client's one change at a time: a lock whose end calls `forget` (what the client
    kept is read anew, so what it changed shows next)."""

    def __init__(self, forget: Callable[[], None]) -> None:
        self._lock, self._forget = asyncio.Lock(), forget

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(self, *exc: object) -> None:
        self._forget()
        self._lock.release()


class _Closed(ConnectionError):
    """The connection ended without a response; whether the request ran is unknown."""


class _Conn:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        self.http = h11.Connection(h11.CLIENT, max_incomplete_event_size=1 << 16)
        self.timer: asyncio.TimerHandle | None = None

    def close(self) -> None:
        if self.timer:
            self.timer.cancel()
        with contextlib.suppress(Exception):
            self.writer.close()


class Pool:
    def __init__(self, host: str, check: Callable[[bytes], None]) -> None:
        self.host, self.check = host, check
        self._idle: dict[int, list[_Conn]] = {}
        self._active: set[_Conn] = set()
        self._closed = False

    def close(self) -> None:
        """Permanently close this client's idle and active connections."""
        self._closed = True
        for c in [*self._active, *(c for conns in self._idle.values() for c in conns)]:
            c.close()
        self._idle.clear()

    def _take(self, port: int) -> _Conn | None:
        conns = self._idle.get(port) or []
        while conns:
            c = conns.pop()
            if c.timer:
                c.timer.cancel()
            if not c.reader.at_eof() and not c.writer.is_closing():
                return c
            c.close()
        return None

    def _give(self, port: int, c: _Conn) -> None:
        if self._closed:
            c.close()
            return
        conns = self._idle.setdefault(port, [])
        conns.append(c)

        def expire() -> None:
            conns.remove(c)
            c.close()

        c.timer = asyncio.get_running_loop().call_later(IDLE_FOR, expire)
        while len(conns) > MAX_IDLE:
            conns.pop(0).close()

    async def _open(self, port: int) -> _Conn:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # checked against the pin before any HTTP bytes are sent
        reader, writer = await asyncio.open_connection(self.host, port, ssl=ctx)
        c = _Conn(reader, writer)
        try:
            if self._closed:
                raise ConnectionError("the device client was closed")
            self.check(writer.get_extra_info("ssl_object").getpeercert(binary_form=True))
        except BaseException:
            c.close()
            raise
        return c

    async def request(
        self,
        port: int,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
        max_bytes: int,
        idempotent: bool | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Status, lower-case headers and a bounded response body. `idempotent` (default: GET
        and HEAD) lets it be sent once more when a kept connection turns out closed."""
        retry = method in {"GET", "HEAD"} if idempotent is None else idempotent
        host = f"[{self.host}]" if ":" in self.host else self.host
        request = h11.Request(
            method=method,
            target=path,
            headers=[
                ("Host", f"{host}:{port}"),
                *headers.items(),
                ("Content-Length", str(len(body))),
            ],
        )
        async with asyncio.timeout(timeout):
            for attempt in range(2):
                if self._closed:
                    raise ConnectionError("the device client was closed")
                c = self._take(port) if attempt == 0 else None
                reused = c is not None
                c = c or await self._open(port)
                self._active.add(c)
                try:
                    status, heads, payload, keep = await _exchange(c, request, body, max_bytes)
                except _Closed:
                    c.close()
                    if reused and retry:
                        continue
                    raise ConnectionError(f"{self.host}: the connection closed without an answer") from None
                except BaseException:
                    c.close()
                    raise
                finally:
                    self._active.discard(c)
                if keep:
                    self._give(port, c)
                else:
                    c.close()
                return status, heads, payload
        raise ConnectionError(f"{self.host}: no answer")


async def _exchange(
    c: _Conn,
    request: h11.Request,
    body: bytes,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes, bool]:
    response = None
    received = False
    payload = bytearray()
    try:
        for event in (request, h11.Data(data=body), h11.EndOfMessage()):
            c.writer.write(c.http.send(event))
        await c.writer.drain()
        while True:
            event = c.http.next_event()
            if event is h11.NEED_DATA:
                try:
                    data = await c.reader.read(65536)
                except (ssl.SSLError, ConnectionResetError):
                    data = b""  # h11 still checks for truncated headers and bodies
                if not data and not received:
                    raise _Closed
                received = received or bool(data)
                c.http.receive_data(data)
            elif isinstance(event, h11.InformationalResponse):
                if event.status_code == 101:
                    raise ConnectionError("HTTP upgrades aren't supported for device requests")
            elif isinstance(event, h11.Response):
                response = event
                length = dict(event.headers).get(b"content-length")
                if length is not None and int(length) > max_bytes and request.method != b"HEAD":
                    raise ValueError("the device's answer is too large")
            elif isinstance(event, h11.Data):
                if len(payload) + len(event.data) > max_bytes:
                    raise ValueError("the device's answer is too large")
                payload.extend(event.data)
            elif isinstance(event, h11.EndOfMessage):
                assert response is not None
                heads = {k.decode("ascii"): v.decode("latin-1") for k, v in response.headers}
                keep = c.http.our_state is h11.DONE and c.http.their_state is h11.DONE
                keep = keep and not c.http.trailing_data[0]
                if keep:
                    c.http.start_next_cycle()
                return response.status_code, heads, bytes(payload), keep
            elif isinstance(event, h11.ConnectionClosed):
                raise _Closed
    except (ConnectionResetError, BrokenPipeError) as e:
        raise _Closed from e
    except h11.RemoteProtocolError as e:
        raise ConnectionError("invalid or incomplete HTTP response") from e
