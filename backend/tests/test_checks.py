import asyncio

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
        writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"
    async with server:
        login_page = await checks.run("127.0.0.1", Check.model_validate({"http": url}))
        strict = await checks.run("127.0.0.1", Check.model_validate({"http": url, "expect": 200}))
    assert login_page.up and login_page.detail == "HTTP 401"
    assert not strict.up


async def test_ping_localhost() -> None:
    result = await checks.run("127.0.0.1", Check.model_validate("ping"))
    # Unprivileged ICMP may be off in a sandbox; then the result must still be a clean failure.
    assert result.up or result.detail
