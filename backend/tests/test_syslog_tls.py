from __future__ import annotations

import asyncio
import contextlib
import ssl
from pathlib import Path

import pytest

from kastellan import syslog_tls


def authority(tmp_path: Path) -> syslog_tls.Authority:
    kept: dict[str, str] = {}

    def secret(name, make):
        return kept.setdefault(name, make())

    return syslog_tls.Authority(tmp_path, secret)


def client_context(tmp_path: Path, bundle: dict[str, str], name: str) -> ssl.SSLContext:
    (tmp_path / f"{name}.pem").write_text(bundle["cert"])
    (tmp_path / f"{name}.key").write_text(bundle["key"])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False  # rsyslog's x509/certvalid: the chain, not the name
    ctx.load_verify_locations(cadata=bundle["ca"])
    ctx.load_cert_chain(tmp_path / f"{name}.pem", tmp_path / f"{name}.key")
    return ctx


async def send(port: int, ctx: ssl.SSLContext, *messages: bytes) -> None:
    _reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
    for m in messages:
        writer.write(m)
    await writer.drain()
    await asyncio.sleep(0.2)
    writer.close()
    with contextlib.suppress(ConnectionError, ssl.SSLError):
        await writer.wait_closed()


async def test_only_issued_certificates_get_in(tmp_path: Path) -> None:
    ca = authority(tmp_path)
    nas = ca.issue("nas")
    old = ca.issue("cam")
    listener = syslog_tls.Listener(ca, lambda d: d in ("nas", "cam"))
    server = await asyncio.start_server(listener.handle, "127.0.0.1", 0, ssl=ca.server_context(["127.0.0.1"]))
    port = server.sockets[0].getsockname()[1]
    try:
        msg = b"<38>1 2026-11-21T10:00:00Z nas sshd 1 - - Accepted publickey for ada"
        await send(
            port, client_context(tmp_path, nas, "nas"), f"{len(msg)} ".encode() + msg, b"<30>cron[2]: ran\n"
        )
        lines = listener.take()
        assert [(ln.device, ln.program, ln.msg, ln.verified) for ln in lines] == [
            ("nas", "sshd", "Accepted publickey for ada", True),
            ("nas", "cron", "ran", True),
        ]
        ca.revoke("cam")  # a revoked certificate: the connection gets nothing in
        await send(port, client_context(tmp_path, old, "cam"), b"<30>x: forged\n")
        assert listener.take() == [] and listener.refused == 1
        stranger = authority(tmp_path / "other").issue("nas")  # another authority's: no handshake
        with pytest.raises((ssl.SSLError, ConnectionError)):
            await send(port, client_context(tmp_path, stranger, "stranger"), b"<30>x: forged\n")
            assert listener.take() == []
            raise ConnectionError("refused after the handshake")
    finally:
        server.close()
    assert ca.state("nas")["issued"] and not ca.state("cam")["issued"]


async def test_revocation_and_stop_end_live_senders(tmp_path: Path) -> None:
    from kastellan.logs_poll import SyslogTls

    ca = authority(tmp_path)
    listener = syslog_tls.Listener(ca, lambda d: d == "nas")
    ctx = client_context(tmp_path, ca.issue("nas"), "nas")
    server = await asyncio.start_server(listener.handle, "127.0.0.1", 0, ssl=ca.server_context(["127.0.0.1"]))
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
    try:
        writer.write(b"<30>x: before\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        assert len(listener.take()) == 1
        ca.revoke("nas")
        writer.write(b"<30>x: after\n")
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert listener.take() == []
        writer.close()
        await writer.wait_closed()
        ctx = client_context(tmp_path, ca.issue("nas"), "nas2")
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
        writer.write(b"<30>x: enabled\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        service = SyslogTls()
        service.server, service.listener = server, listener
        await service.stop()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert not listener.active
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        server.close()
        await server.wait_closed()


def test_concurrent_certificates_share_the_authority(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding

    ca = authority(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        bundles = list(pool.map(ca.issue, ["nas"] * 20))
    assert len({bundle["ca"] for bundle in bundles}) == 1
    for bundle in bundles:
        der = x509.load_pem_x509_certificate(bundle["cert"].encode()).public_bytes(Encoding.DER)
        assert ca.device_of(der) == "nas"
    assert ca.revoke("nas") == 20
    ca.issue("nas")
    assert ca.revoke("nas") == 1  # this time's, not all so far
