"""The Guacamole protocol, for remote desktops through guacd (Apache Guacamole's daemon).

guacd does the hard part (RDP, VNC); it speaks a simple text protocol of instructions:

    4.size,4.1024,3.768,2.96;

Each element is its length in Unicode code points, a dot, and the value; elements are separated
by commas, an instruction ends with a semicolon. The hub opens a connection to guacd, does the
handshake (`select`, `args`, `size`..., `connect`, `ready`), and from then on relays the
instructions between guacd and the browser, where guacamole-common-js draws the screen and sends
mouse and keyboard.

The protocol is documented at https://guacamole.apache.org/doc/gug/guacamole-protocol.html.
"""

from __future__ import annotations

import asyncio
import codecs
from collections.abc import Sequence

INTERNAL = ""  # opcode of the tunnel's own instructions (ping, uuid), never sent to guacd
MAX_INSTRUCTION = 64 * 1024 * 1024  # guacd sends images in chunks; this is far above any of them


class ProtocolError(Exception):
    pass


def encode(*elements: str) -> str:
    return ",".join(f"{len(e)}.{e}" for e in elements) + ";"


def for_browser(instruction: str) -> str:
    """guacd counts lengths in code points; guacamole-common-js 1.5.0 counts UTF-16 units, so a
    character outside the BMP (an emoji in a VNC desktop's name) would shift its parsing and let
    the rest of an element be read as instructions. Re-count lengths in UTF-16 units for those."""
    if instruction.isascii() or all(ord(ch) <= 0xFFFF for ch in instruction):
        return instruction
    elements = decode(instruction)
    return ",".join(f"{len(e.encode('utf-16-le')) // 2}.{e}" for e in elements) + ";"


def _length(text: str) -> int:
    # Only ASCII digits, as guacd reads them: int() would also take " 3", "+3", "1_0" or "٣".
    if not text or len(text) > 10 or not (text.isascii() and text.isdigit()):
        raise ProtocolError("bad length")
    return int(text)


def decode(instruction: str) -> list[str]:
    """One complete instruction ("4.size,1.1;") to its elements (["size", "1"])."""
    elements: list[str] = []
    i = 0
    while True:
        dot = instruction.find(".", i)
        if dot < 0:
            raise ProtocolError("no length")
        length = _length(instruction[i:dot])
        start, end = dot + 1, dot + 1 + length
        if end >= len(instruction):
            raise ProtocolError("truncated")
        elements.append(instruction[start:end])
        if instruction[end] == ";":
            if end != len(instruction) - 1:
                raise ProtocolError("text after the end")
            return elements
        if instruction[end] != ",":
            raise ProtocolError("missing separator")
        i = end + 1


class Reader:
    """Splits a stream into whole instructions. Lengths count code points, so the stream is read
    as text."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self.reader = reader
        self.buffer = ""
        # Incremental: a character split between two reads must not turn into U+FFFD twice,
        # which would make the code-point count disagree with the length.
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    async def instruction(self) -> str:
        """The next whole instruction, as text; "" at the end of the stream."""
        while True:
            end = complete(self.buffer)
            if end is not None:
                instruction, self.buffer = self.buffer[:end], self.buffer[end:]
                return instruction
            if len(self.buffer) > MAX_INSTRUCTION:
                raise ProtocolError("instruction too long")
            chunk = await self.reader.read(65536)
            if not chunk:
                return ""
            self.buffer += self._decoder.decode(chunk)


def complete(buffer: str) -> int | None:
    """The end (exclusive) of the first whole instruction in buffer, or None."""
    i = 0
    while True:
        dot = buffer.find(".", i)
        if dot < 0:
            return None
        length = _length(buffer[i:dot])
        end = dot + 1 + length
        if end >= len(buffer):
            return None
        if buffer[end] == ";":
            return end + 1
        if buffer[end] != ",":
            raise ProtocolError("missing separator")
        i = end + 1


async def handshake(
    reader: Reader,
    writer: asyncio.StreamWriter,
    protocol: str,
    params: dict[str, str],
    size: Sequence[int],
) -> str:
    """Select the protocol, answer guacd's `args` with `params`, and wait for `ready`. Returns
    guacd's connection id."""
    writer.write(encode("select", protocol).encode())
    await writer.drain()
    args = decode(await _expect(reader))
    if args[0] != "args":
        raise ProtocolError(f"expected args, got {args[0]}")
    # The first arg is guacd's protocol version (VERSION_1_5_0); we answer with the same.
    names = args[1:]
    width, height, dpi = size
    writer.write(encode("size", str(width), str(height), str(dpi)).encode())
    writer.write(encode("audio").encode())
    writer.write(encode("video").encode())
    writer.write(encode("image", "image/png", "image/jpeg", "image/webp").encode())
    # Values in the order guacd named them. Since 1.1 the first name is guacd's protocol version
    # (VERSION_1_5_0), answered with itself; older guacd sends only parameter names.
    if names and names[0].startswith("VERSION_"):
        values = [names[0]] + [params.get(name, "") for name in names[1:]]
    else:
        values = [params.get(name, "") for name in names]
    writer.write(encode("connect", *values).encode())
    await writer.drain()
    ready = decode(await _expect(reader))
    if ready[0] == "error":
        raise ProtocolError(ready[1] if len(ready) > 1 else "guacd refused the connection")
    if ready[0] != "ready" or len(ready) < 2:
        raise ProtocolError(f"expected ready, got {ready[0]}")
    return ready[1]


async def _expect(reader: Reader) -> str:
    instruction = await asyncio.wait_for(reader.instruction(), 15)
    if not instruction:
        raise ProtocolError("guacd closed the connection")
    return instruction
