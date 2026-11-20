"""Certificates: valid or why not, and when they end; which checks watch them; their alarms."""

import asyncio
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from kastellan import checks, tls
from kastellan.alarms import Severity, evaluate
from kastellan.config import Check, Config
from kastellan.monitor import CheckStatus, DeviceStatus


def self_signed(folder: Path, name: str, days: int) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (folder / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem = serialization.Encoding.PEM
    plain = serialization.NoEncryption()
    (folder / "k.pem").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8, plain))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(folder / "c.pem", folder / "k.pem")
    return ctx


async def test_a_self_signed_certificate_is_seen_and_described(tmp_path: Path) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=self_signed(tmp_path, "box.lan", 30))
    port = server.sockets[0].getsockname()[1]
    tls._cache.clear()
    async with server:
        result = await checks.run("127.0.0.1", Check.model_validate({"http": f"https://127.0.0.1:{port}/"}))
    assert result.up and result.detail == "HTTP 200"
    assert result.address is None  # the target is an address already
    cert = result.cert
    assert cert is not None and not cert.valid and "self-signed" in (cert.error or "")
    assert cert.names == ("box.lan",) and cert.issuer == "box.lan"
    assert 28 <= cert.view(datetime.now(UTC))["days_left"] <= 30


async def test_no_tls_at_all_is_no_certificate() -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    tls._cache.clear()
    async with server:
        assert await tls.certificate("127.0.0.1", port, "127.0.0.1", timeout=2) is None


@pytest.mark.parametrize(
    ("host", "public"),
    [
        ("norn.stefl.at", True),
        ("example.com.", True),
        ("nas.lan", False),
        ("printer.local", False),
        ("fritz.box", False),
        ("router.home.arpa", False),
        ("nas", False),
        ("192.168.1.1", False),
        ("[::1]", False),
    ],
)
def test_public_names(host: str, public: bool) -> None:
    assert tls.public_name(host) is public


def test_which_checks_watch_their_certificate() -> None:
    def watched(**kw) -> bool:
        return Check.model_validate(kw).watch_cert

    assert watched(http="https://norn.stefl.at")
    assert not watched(http="https://192.168.15.1")
    assert not watched(http="https://nas.lan")
    assert watched(http="https://nas.lan", tls=True)
    assert watched(http="https://nas.lan", verify=True)
    assert not watched(http="https://norn.stefl.at", tls=False)
    assert not watched(http="http://norn.stefl.at")
    with pytest.raises(ValueError, match="https"):
        Check.model_validate({"http": "http://x.example", "tls": True})


NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
CONFIG = Config.model_validate(
    {
        "devices": {
            "web": {
                "host": "w",
                "checks": [
                    "ping",
                    {"http": "https://site.example", "name": "public", "verify": True},
                    {"http": "https://w.lan", "name": "local"},
                ],
            }
        }
    }
)


def problems(cert: tls.Cert, up: bool = True) -> list[tuple[str, Severity, str]]:
    st = {
        "web": DeviceStatus(
            checks={
                name: CheckStatus(up=up, since=NOW - timedelta(hours=1), checked=NOW, cert=cert)
                for name in ("ping", "public", "local")
            }
        )
    }
    found = evaluate(CONFIG, st, NOW, {}, NOW - timedelta(hours=2))
    return [(p.key, p.severity, p.title) for p in found]


def cert(valid: bool = True, days: float = 60, error: str | None = None) -> tls.Cert:
    return tls.Cert(valid, error, NOW + timedelta(days=days), "Let's Encrypt", ("site.example",))


def test_certificate_alarms() -> None:
    assert problems(cert()) == []
    # Only the watched check (a public name), not the .lan one with the same certificate.
    assert problems(cert(days=5)) == [
        ("cert:web:public", Severity.WARNING, "web: public: certificate ends in 5 days")
    ]
    assert problems(cert(days=0.5))[0][2].endswith("ends in less than a day")
    assert problems(cert(False, error="certificate has expired", days=-1)) == [
        ("cert:web:public", Severity.CRITICAL, "web: public: certificate not valid")
    ]
    # A device that is down raises its own alarm; no certificate alarms on top.
    assert [k for k, _, _ in problems(cert(days=5), up=False)] == ["down:web"]


def test_certificate_alarms_over_time(tmp_path: Path) -> None:
    """evaluate, carry and Alarms.update together, as the hub runs them."""
    from kastellan.alarms import Alarms

    alarms = Alarms(tmp_path)
    key = "cert:web:public"

    def step(t: datetime, up: bool, c: tls.Cert | None, checked: datetime | None = None):
        st = {
            "web": DeviceStatus(
                checks={
                    "ping": CheckStatus(up=True, since=NOW - timedelta(days=3), checked=t),
                    "public": CheckStatus(up=up, since=NOW - timedelta(days=3), checked=checked or t, cert=c),
                    "local": CheckStatus(up=True, since=NOW - timedelta(days=3), checked=t),
                }
            )
        }
        unjudged: set[str] = set()
        found = evaluate(CONFIG, st, t, {}, NOW - timedelta(hours=2), unjudged)
        return alarms.update(found + alarms.carry(unjudged), t)

    sent = step(NOW, True, cert(days=2))
    assert [m.severity for m in sent] == [Severity.WARNING] and alarms.open[key].severity == "warning"
    # It expires, and the request fails over it (verify: true): critical, said again, and no
    # second "service down" alarm about the same thing.
    t = NOW + timedelta(minutes=10)
    sent = step(t, False, cert(False, error="certificate has expired", days=-1))
    assert [(m.severity, m.title) for m in sent] == [
        (Severity.CRITICAL, "web: public: certificate not valid")
    ]
    assert set(alarms.open) == {key}
    # The service goes away (its own alarm); the certificate can't be seen: its alarm stays.
    for minutes in (20, 30, 60):
        sent = step(NOW + timedelta(minutes=minutes), False, None)
        assert all(not m.title.startswith("web: public: cert") for m in sent)
    assert alarms.open[key].severity == "critical"
    step(NOW + timedelta(minutes=70), True, None)  # back, the certificate not looked at yet
    assert alarms.open[key].severity == "critical"
    # Old results (the check stuck) don't resolve it either.
    sent = step(NOW + timedelta(hours=2), True, cert(), checked=NOW - timedelta(hours=1))
    assert all("cert" not in m.title for m in sent) and alarms.open[key].severity == "critical"
    # A good certificate: it ends, once the problem has been gone a while.
    step(NOW + timedelta(hours=3), True, cert())
    sent = step(NOW + timedelta(hours=3, minutes=5), True, cert())
    assert [m.title for m in sent] == ["web: public: certificate is fine again"]
    assert alarms.open == {}


async def test_a_slow_handshake_never_makes_a_service_down(tmp_path: Path) -> None:
    """A slow embedded web UI: the certificate look runs out of time, the check is still up."""
    ctx = self_signed(tmp_path, "slow.lan", 30)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve(conn: socket.socket) -> None:
        time.sleep(1.2)  # a slow device: every handshake takes this long
        try:
            with ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(4096)
                s.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass

    def accept() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    tls._cache.clear()
    check = Check.model_validate({"http": f"https://127.0.0.1:{port}/", "timeout": 2})
    try:
        result = await checks.run("127.0.0.1", check)
        assert result.up and result.detail == "HTTP 200"
        # The look at the certificate goes on, and a later run has it.
        await asyncio.sleep(3)
        again = await checks.run("127.0.0.1", check)
    finally:
        stop.set()
        listener.close()
    assert again.up and again.cert is not None and not again.cert.valid


def test_a_new_target_forgets_the_old_certificate(tmp_path: Path) -> None:
    from kastellan.history import History
    from kastellan.monitor import Monitor

    def config(url: str) -> Config:
        return Config.model_validate(
            {"devices": {"web": {"host": "w", "checks": [{"http": url, "name": "site"}]}}}
        )

    monitor = Monitor(History(tmp_path))
    monitor.prepare(config("https://a.example"))
    monitor.status["web"].checks["site"].cert = cert()
    monitor.status["web"].checks["site"].address = "203.0.113.1"
    monitor.prepare(config("https://a.example"))
    assert monitor.status["web"].checks["site"].cert is not None  # same target: kept
    monitor.prepare(config("https://b.example"))
    assert monitor.status["web"].checks["site"].cert is None
    assert monitor.status["web"].checks["site"].address is None
