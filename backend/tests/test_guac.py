import asyncio

import pytest

from kastellan import guac


def test_encode_decode_counts_code_points() -> None:
    text = guac.encode("clipboard", "grüße 👋")
    assert text == "9.clipboard,7.grüße 👋;"
    assert guac.decode(text) == ["clipboard", "grüße 👋"]
    for bad in ("4.size", "x.size;", "4.size,1.1;junk", "9.size;"):
        with pytest.raises(guac.ProtocolError):
            guac.decode(bad)


async def test_reader_splits_a_stream() -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(b"4.sync,3.123;5.mouse,1.1,1.2;"[:9])
    stream.feed_data(b"4.sync,3.123;5.mouse,1.1,1.2;"[9:])
    stream.feed_eof()
    reader = guac.Reader(stream)
    assert await reader.instruction() == "4.sync,3.123;"
    assert await reader.instruction() == "5.mouse,1.1,1.2;"
    assert await reader.instruction() == ""


async def fake_guacd(log: list[list[str]], answer: str = "ready"):
    """A guacd that does the handshake like 1.6 and then echoes instructions."""

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        reader = guac.Reader(r)
        log.append(guac.decode(await reader.instruction()))  # select
        w.write(guac.encode("args", "VERSION_1_5_0", "hostname", "port", "username", "password").encode())
        while True:
            instruction = guac.decode(await reader.instruction())
            log.append(instruction)
            if instruction[0] == "connect":
                break
        if answer == "ready":
            w.write(guac.encode("ready", "$abc").encode())
        else:
            w.write(guac.encode("error", "Aborted. See logs.", "519").encode())
        await w.drain()
        while instruction := await reader.instruction():
            w.write(instruction.encode())  # echo
            await w.drain()
        w.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_handshake() -> None:
    log: list[list[str]] = []
    server, port = await fake_guacd(log)
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        params = {"hostname": "pc.lan", "port": "3389", "username": "andreas", "password": "pw"}
        assert (
            await guac.handshake(guac.Reader(r), w, "rdp", params, (1280, 720, 96), "Europe/Vienna") == "$abc"
        )
        w.close()
    assert log[0] == ["select", "rdp"]
    assert ["size", "1280", "720", "96"] in log
    assert ["timezone", "Europe/Vienna"] in log
    assert log[-1] == ["connect", "VERSION_1_5_0", "pc.lan", "3389", "andreas", "pw"]


async def test_handshake_error() -> None:
    server, port = await fake_guacd([], answer="error")
    async with server:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        with pytest.raises(guac.ProtocolError, match="Aborted"):
            await guac.handshake(guac.Reader(r), w, "rdp", {}, (800, 600, 96))
        w.close()


async def test_reader_keeps_characters_split_between_reads() -> None:
    """Security review: a character across a read boundary used to break the length count."""
    text = guac.encode("name", "é" * 40000)
    data = text.encode()
    stream = asyncio.StreamReader()
    for i in range(0, len(data), 65535):  # odd chunk size: splits "é" (2 bytes)
        stream.feed_data(data[i : i + 65535])
    stream.feed_eof()
    reader = guac.Reader(stream)
    reader.reader._limit = 1 << 30
    assert guac.decode(await reader.instruction()) == ["name", "é" * 40000]


def test_lengths_only_ascii_digits() -> None:
    for bad in ("+3.abc;", " 3.abc;", "1_0.abcdefghij;", "٣.abc;", "99999999999.a;"):
        with pytest.raises(guac.ProtocolError):
            guac.decode(bad)


def test_browser_lengths_count_utf16_units() -> None:
    assert guac.for_browser("4.name,2.ab;") == "4.name,2.ab;"
    assert guac.for_browser(guac.encode("name", "a😀b")) == "4.name,4.a😀b;"  # 😀 is two units
