"""Kept connections to a device: one handshake for many requests, the pin on each new one, and
a connection the server closed meanwhile replaced without losing the request."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from test_tls import self_signed

from kastellan import pinned_http
from kastellan.pinned_http import Pool


async def server(
    tmp_path: Path,
    answers: list[bytes | None],
    close_after: int | None = None,
    received: asyncio.Event | None = None,
):
    """A TLS server that answers each request with the next of `answers`, keeping connections
    open (closing one after `close_after` requests). Counts connections and requests."""
    ctx = self_signed(tmp_path, "router", 30)
    der = x509.load_pem_x509_certificate((tmp_path / "c.pem").read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    seen = {"connections": 0, "requests": 0}

    async def serve(reader, writer) -> None:
        seen["connections"] += 1
        served = 0
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                n = int(
                    next(
                        ln.split(b":")[1]
                        for ln in head.split(b"\r\n")
                        if ln.lower().startswith(b"content-length")
                    )
                )
                await reader.readexactly(n)
                answer = answers[seen["requests"] % len(answers)]
                seen["requests"] += 1
                if received is not None:
                    received.set()
                if answer is None:  # request applied, connection lost before the response
                    break
                writer.write(answer)
                served += 1
                await writer.drain()
                if close_after and served >= close_after:
                    break
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        writer.close()

    srv = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=ctx)
    return srv, srv.sockets[0].getsockname()[1], hashlib.sha256(der).hexdigest(), seen


def pinned(fp: str):
    def check(der: bytes) -> None:
        if hashlib.sha256(der).hexdigest() != fp:
            raise ValueError("another certificate")

    return check


async def test_one_connection_for_many_requests(tmp_path: Path) -> None:
    sized = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n"
    srv, port, fp, seen = await server(tmp_path, [sized, chunked])
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        bodies = [(await pool.request(port, "POST", "/ubus", {}, b"{}", 5, 1000))[2] for _ in range(6)]
        assert bodies == [b"hello", b"abcde"] * 3
        assert seen == {"connections": 1, "requests": 6}
        pool.close()


async def test_a_closed_connection_is_replaced_and_the_pin_checked(tmp_path: Path) -> None:
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    srv, port, fp, seen = await server(tmp_path, [ok], close_after=1)
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        for _ in range(3):
            assert (await pool.request(port, "GET", "/", {}, b"", 5, 1000))[:2][0] == 200
            await asyncio.sleep(0.05)  # the server has closed it by now
        assert seen == {"connections": 3, "requests": 3}  # each request answered once
        with pytest.raises(ValueError):
            await Pool("127.0.0.1", pinned("0" * 64)).request(port, "GET", "/", {}, b"", 5, 1000)
        assert seen["requests"] == 3  # nothing sent to another certificate
        pool.close()


async def test_connection_close_and_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closing = b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\nok"
    srv, port, fp, seen = await server(tmp_path, [closing])
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        for _ in range(2):
            await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        assert seen["connections"] == 2  # not kept when the server says so
        with pytest.raises(ValueError, match="too large"):
            await pool.request(port, "GET", "/", {}, b"", 5, 1)
        monkeypatch.setattr(pinned_http, "IDLE_FOR", 0.0)  # stale: a new one
        await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        pool.close()


@pytest.mark.parametrize(
    ("method", "idempotent", "retried"),
    [("GET", None, True), ("POST", None, False), ("POST", True, True), ("GET", False, False)],
)
async def test_only_reads_retry_a_lost_response(
    tmp_path: Path, method: str, idempotent: bool | None, retried: bool
) -> None:
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    srv, port, fp, seen = await server(tmp_path, [ok, None, ok])
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        if retried:
            answer = await pool.request(port, method, "/", {}, b"{}", 5, 1000, idempotent=idempotent)
            assert answer[2] == b"ok"
            assert seen == {"connections": 2, "requests": 3}
        else:
            with pytest.raises(ConnectionError):
                await pool.request(port, method, "/action", {}, b"{}", 5, 1000, idempotent=idempotent)
            assert seen == {"connections": 1, "requests": 2}
        pool.close()


async def test_informational_responses_do_not_desynchronize_requests(tmp_path: Path) -> None:
    info = b"HTTP/1.1 103 Early Hints\r\nLink: </style.css>\r\n\r\n"
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    srv, port, fp, seen = await server(tmp_path, [info + ok, ok])
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        for _ in range(2):
            status, _, payload = await pool.request(port, "POST", "/", {}, b"", 5, 1000)
            assert (status, payload) == (200, b"ok")
        assert seen == {"connections": 1, "requests": 2}
        pool.close()


@pytest.mark.parametrize(
    "answer",
    [
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\nok!",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nokXX0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nok",
    ],
)
async def test_bad_framing_closes_the_connection(tmp_path: Path, answer: bytes) -> None:
    srv, port, fp, seen = await server(tmp_path, [answer], close_after=1)
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        with pytest.raises(ConnectionError):
            await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        assert seen["requests"] == 1  # no replay of partial responses
        pool.close()


async def test_idle_connections_expire_and_closed_clients_stay_closed(tmp_path: Path, monkeypatch) -> None:
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"
    srv, port, fp, seen = await server(tmp_path, [ok])
    monkeypatch.setattr(pinned_http, "IDLE_FOR", 0.01)
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        conn = pool._idle[port][0]
        await asyncio.sleep(0.05)
        assert conn.writer.is_closing() and not pool._idle[port]
        pool.close()
        with pytest.raises(ConnectionError, match="closed"):
            await pool.request(port, "GET", "/", {}, b"", 5, 1000)
        assert seen["requests"] == 1


async def test_close_cancels_an_active_request(tmp_path: Path) -> None:
    # A response with an unfinished body keeps the request active until the pool closes.
    partial = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\no"
    received = asyncio.Event()
    srv, port, fp, _seen = await server(tmp_path, [partial], received=received)
    async with srv:
        pool = Pool("127.0.0.1", pinned(fp))
        task = asyncio.create_task(pool.request(port, "POST", "/action", {}, b"{}", 5, 1000))
        async with asyncio.timeout(2):
            await received.wait()
        pool.close()
        with pytest.raises(ConnectionError):
            await task
        assert not pool._active and not pool._idle
