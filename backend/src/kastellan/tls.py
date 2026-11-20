"""The certificate an https service presents: is it valid (a trusted chain, for this name, not
expired), and when does it end. Checked with its own handshake next to the HTTP request, so it
is known whatever the check's `verify` says, and why it is not valid ("self-signed",
"hostname mismatch", "expired") rather than only that the request failed.

Results are kept for a few minutes per address and name: a certificate changes rarely, and
checks run every minute.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import ssl
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import certifi
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

KEPT = 600.0  # seconds a result is reused
KEPT_FAILED = 120.0  # ... and a failed look (no TLS, too slow), so it isn't repeated every minute
_cache: dict[tuple[str, int, str], tuple[float, Cert | None]] = {}

# Names that only mean something at home: their certificates are usually self-signed, so they
# aren't watched unless the check says `tls: true`.
LOCAL_SUFFIXES = (".lan", ".local", ".home", ".home.arpa", ".internal", ".localdomain", ".box", ".localhost")


@dataclass(frozen=True)
class Cert:
    valid: bool
    error: str | None  # why not valid
    expires: datetime | None
    issuer: str | None
    names: tuple[str, ...]  # the names it is for

    def view(self, now: datetime) -> dict[str, Any]:
        days = (self.expires - now).total_seconds() / 86400 if self.expires else None
        return {
            "valid": self.valid,
            "error": self.error,
            "expires": self.expires.isoformat(timespec="seconds") if self.expires else None,
            "days_left": None if days is None else int(days // 1),
            "issuer": self.issuer,
            "names": list(self.names[:20]),
        }


def public_name(host: str) -> bool:
    """A name on the internet, not an address or a name only the home network knows."""
    name = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(name.strip("[]"))
        return False
    except ValueError:
        pass
    return "." in name and not name.endswith(LOCAL_SUFFIXES)


_verifying_context: ssl.SSLContext | None = None


def _verifying() -> ssl.SSLContext:
    """Made once: loading the CA bundle takes a while, and it would block the event loop."""
    global _verifying_context
    if _verifying_context is None:
        _verifying_context = ssl.create_default_context(cafile=certifi.where())
    return _verifying_context


def _accepting() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _handshake(host: str, port: int, name: str, ctx: ssl.SSLContext) -> bytes:
    """The server's certificate (DER) after a handshake with this context."""
    _, writer = await asyncio.open_connection(host, port, ssl=ctx, server_hostname=name)
    try:
        obj = writer.get_extra_info("ssl_object")
        der = obj.getpeercert(binary_form=True) if obj else None
    finally:
        writer.close()
        with contextlib.suppress(OSError, TimeoutError, ssl.SSLError):
            await asyncio.wait_for(writer.wait_closed(), 0.5)
    if not der:
        raise ssl.SSLError("no certificate")
    return der


def _describe(der: bytes, valid: bool, error: str | None) -> Cert:
    cert = x509.load_der_x509_certificate(der)
    by = cert.issuer.get_attributes_for_oid
    issuer = by(NameOID.ORGANIZATION_NAME) or by(NameOID.COMMON_NAME)
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = tuple(san.get_values_for_type(x509.DNSName)) + tuple(
            str(a) for a in san.get_values_for_type(x509.IPAddress)
        )
    except x509.ExtensionNotFound:
        names = tuple(str(a.value) for a in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME))
    return Cert(
        valid,
        error,
        cert.not_valid_after_utc,
        str(issuer[0].value) if issuer else None,
        names,
    )


async def certificate(host: str, port: int, name: str, timeout: float = 5) -> Cert | None:
    """The certificate at host:port for `name` (SNI and the name it must be for), within
    `timeout` seconds in all. None if no TLS handshake works (the check says why the service is
    down), or it takes too long."""
    key = (host, port, name)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < (KEPT if hit[1] else KEPT_FAILED):
        return hit[1]
    cert: Cert | None = None
    try:
        async with asyncio.timeout(timeout):
            try:
                cert = _describe(await _handshake(host, port, name, _verifying()), True, None)
            except ssl.SSLCertVerificationError as e:
                reason = str(e.verify_message or e.reason or "not trusted")
                cert = Cert(False, reason, None, None, ())
                cert = _describe(await _handshake(host, port, name, _accepting()), False, reason)
    except (OSError, TimeoutError, ssl.SSLError, ValueError):
        pass  # not TLS, or too slow: None, or what is known (not valid, and why)
    if len(_cache) > 1000:
        _cache.clear()
    _cache[key] = (time.monotonic(), cert)
    return cert
