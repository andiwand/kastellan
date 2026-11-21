import asyncio
import time

from kastellan import checks
from kastellan.config import Check


async def test_tcp_up_and_down() -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        up = await checks.run("127.0.0.1", Check.model_validate({"tcp": port}))
    assert up.up and up.latency_ms is not None
    down = await checks.run("127.0.0.1", Check.model_validate({"tcp": port}))
    assert not down.up and down.detail


async def test_http_status() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 401 Unauthorized\r\nServer: nginx/1.24.0 (Ubuntu)\r\n"
            b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    async with server:
        login_page = await checks.run("127.0.0.1", Check.model_validate({"http": url}))
        strict = await checks.run("127.0.0.1", Check.model_validate({"http": url, "expect": 200}))
    assert login_page.up and login_page.detail == "HTTP 401"
    assert str(login_page.software) == "nginx 1.24.0 (Ubuntu)"
    assert not strict.up


async def test_ping_localhost() -> None:
    result = await checks.run("127.0.0.1", Check.model_validate("ping"))
    # Unprivileged ICMP may be off in a sandbox; then the result must still be a clean failure.
    assert result.up or result.detail


async def test_tcp_reads_a_greeting() -> None:
    async def greet(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"SSH-2.0-OpenSSH_9.8\r\n")
        await writer.drain()
        await asyncio.sleep(0.2)
        writer.close()

    server = await asyncio.start_server(greet, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        result = await checks.run("127.0.0.1", Check.model_validate({"tcp": port}))
    assert result.up and str(result.software) == "OpenSSH 9.8"


async def test_http_with_a_wrong_charset_still_works() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"<title>Box</title>"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-16\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    async with server:
        result = await checks.run("127.0.0.1", Check.model_validate({"http": url}))
    assert result.up


async def test_dns(monkeypatch) -> None:
    import dns.asyncresolver
    import dns.exception
    import dns.resolver

    asked: list[tuple[str, str, list[str]]] = []

    class Fake:
        def __init__(self, configure: bool = True) -> None:
            self.nameservers: list[str] = []

        async def resolve(self, name: str, rdtype: str, search: bool = False):
            assert search  # a single name gets the search domains
            asked.append((name, rdtype, self.nameservers))
            if name == "gone.example":
                raise dns.resolver.NXDOMAIN
            if rdtype == "AAAA" and name == "router.example":
                await asyncio.sleep(30)  # a router that drops AAAA questions: no answer at all
                raise dns.exception.Timeout
            if rdtype == "AAAA":
                raise dns.resolver.NoAnswer
            return [type("R", (), {"to_text": lambda self: "192.0.2.1"})()]

    monkeypatch.setattr(dns.asyncresolver, "Resolver", Fake)
    up = await checks.run("x", Check.model_validate({"dns": "www.example", "server": "9.9.9.9"}))
    assert up.up and up.detail == "192.0.2.1" and asked[0] == ("www.example", "A", ["9.9.9.9"])
    wrong = await checks.run("x", Check.model_validate({"dns": "www.example", "expect": "192.0.2.9"}))
    assert not wrong.up and "not 192.0.2.9" in wrong.detail
    gone = await checks.run("x", Check.model_validate({"dns": "gone.example"}))
    assert not gone.up and gone.detail == "no such name"
    started = time.perf_counter()
    dropped = await checks.run("x", Check.model_validate({"dns": "router.example"}))
    assert dropped.up and dropped.detail == "192.0.2.1" and dropped.latency_ms < 100
    assert time.perf_counter() - started < 2  # AAAA got a moment, not the time limit


async def test_http_page_must_contain_a_text() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b"<html><title>Home Assistant</title>Bad gateway</html>"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nServer: nginx/1.24.0\r\n"
            + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    async with server:
        ok = await checks.run("127.0.0.1", Check.model_validate({"http": url, "contains": "Home Assistant"}))
        bad = await checks.run("127.0.0.1", Check.model_validate({"http": url, "contains": "Welcome"}))
    assert ok.up and str(ok.software).startswith("nginx")
    assert not bad.up and bad.detail == "HTTP 200, without 'Welcome'"
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Check.model_validate({"tcp": 22, "contains": "x"})
