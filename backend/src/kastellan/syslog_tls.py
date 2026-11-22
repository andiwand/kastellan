"""Syslog over TLS with client certificates (RFC 5425, TCP 6514): encrypted, and each sender
proves which device it is.

Kastellan keeps a certificate authority of its own for this, and only for this: its certificate
in `<data>/syslog/ca.pem`, its key as the secret `syslog-ca-key` (in the key ring when there is
one). A device gets a client certificate made for it (`issue`: the common name is the device's
id; the key is handed out once and never kept), installed in its rsyslog or syslog-ng. The
listener asks every sender for a certificate from this authority, takes its device from the
common name, and refuses certificates that were never issued for that device or were revoked
(`issued.json`). Lines that arrive this way are verified: the certificate says who sent them,
not the address.

The listener's own certificate is made at each start, signed by the same authority (senders
check it against the authority's certificate they got with theirs); its key lives in memory
and a file only for the moment the TLS library loads it.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import json
import os
import ssl
import tempfile
import threading
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import logs
from .secrets import SYSLOG_CA_KEY

MAX_MESSAGE = 64 * 1024
MAX_CONNECTIONS = 64


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return call


class Authority:
    """The certificate authority, and which certificates it issued to which device."""

    def __init__(self, folder: Path, secret: Callable[[str, Callable[[], str]], str]) -> None:
        self.folder = folder
        self._lock = threading.RLock()
        self._secret = secret  # (name, make) -> value: hub.own_secret
        self._cache: tuple[int, dict[str, Any]] | None = None  # issued.json's mtime and content

    @locked
    def _ca(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        key = serialization.load_pem_private_key(
            self._secret(SYSLOG_CA_KEY, lambda: _pem_key(ec.generate_private_key(ec.SECP256R1()))).encode(),
            None,
        )
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        path = self.folder / "ca.pem"
        with contextlib.suppress(OSError, ValueError):
            cert = x509.load_pem_x509_certificate(path.read_bytes())
            if cert.public_key() == key.public_key():
                return cert, key
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Kastellan syslog")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1))
            .not_valid_after(_now() + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        self.folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        return cert, key

    def ca_pem(self) -> str:
        return self._ca()[0].public_bytes(serialization.Encoding.PEM).decode()

    def _signed(
        self,
        common_name: str,
        public_key,
        usage,
        names: list[x509.GeneralName] | None = None,
        days: int = 825,
    ) -> x509.Certificate:
        ca, ca_key = self._ca()
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
            .issuer_name(ca.subject)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(minutes=5))
            .not_valid_after(_now() + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        )
        if names:
            builder = builder.add_extension(x509.SubjectAlternativeName(names), critical=False)
        return builder.sign(ca_key, hashes.SHA256())

    # --- what was issued ----------------------------------------------------------------------

    def _issued(self) -> dict[str, Any]:
        """issued.json, read again only when it changed (the listener asks for every message).
        Read-only: changes go through `_changed`."""
        path = self.folder / "issued.json"
        try:
            stamp = path.stat().st_mtime_ns
            if self._cache is None or self._cache[0] != stamp:
                self._cache = (stamp, json.loads(path.read_text()))
            return self._cache[1]
        except (OSError, ValueError):
            return {"devices": {}}

    def _changed(self) -> dict[str, Any]:
        """issued.json as it is on disk now, to change and `_save`."""
        try:
            return json.loads((self.folder / "issued.json").read_text())
        except (OSError, ValueError):
            return {"devices": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / "issued.json"
        tmp = self.folder / f".issued.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
        self._cache = (path.stat().st_mtime_ns, data)

    @locked
    def issue(self, device: str) -> dict[str, str]:
        """A client certificate for `device` (and its key, only now). Earlier ones stay valid
        until revoked."""
        key = ec.generate_private_key(ec.SECP256R1())
        cert = self._signed(device, key.public_key(), ExtendedKeyUsageOID.CLIENT_AUTH)
        data = self._changed()
        entry = data["devices"].setdefault(device, {"serials": [], "revoked": []})
        entry["serials"].append(format(cert.serial_number, "x"))
        entry["issued"] = _now().isoformat(timespec="seconds")
        self._save(data)
        return {
            "ca": self.ca_pem(),
            "cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "key": _pem_key(key),
        }

    @locked
    def revoke(self, device: str) -> int:
        """Every certificate of `device` refused from now on; how many were revoked now."""
        data = self._changed()
        entry = data["devices"].get(device)
        if not entry or not entry["serials"]:
            return 0
        fresh = set(entry["serials"]) - set(entry.get("revoked", []))
        entry["revoked"] = sorted(set(entry.get("revoked", [])) | fresh)
        entry["serials"] = []
        self._save(data)
        return len(fresh)

    def devices(self) -> set[str]:
        """The devices with a certificate that isn't revoked."""
        return {d for d, e in self._issued()["devices"].items() if e.get("serials")}

    def state(self, device: str) -> dict[str, Any]:
        entry = self._issued()["devices"].get(device) or {}
        return {"issued": bool(entry.get("serials")), "issued_at": entry.get("issued")}

    def device_of(self, der: bytes | None) -> str | None:
        """The device a (chain-checked) client certificate was issued to, or None."""
        if not der:
            return None
        cert = x509.load_der_x509_certificate(der)
        if not cert.not_valid_before_utc <= _now() <= cert.not_valid_after_utc:
            return None
        names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if not names:
            return None
        device = str(names[0].value)
        entry = self._issued()["devices"].get(device) or {}
        serial = format(cert.serial_number, "x")
        if serial in entry.get("serials", []) and serial not in entry.get("revoked", []):
            return device
        return None

    @locked
    def server_context(self, names: list[str]) -> ssl.SSLContext:
        """TLS for the listener: its certificate made now, client certificates required."""
        key = ec.generate_private_key(ec.SECP256R1())
        alt: list[x509.GeneralName] = []
        for n in {*names, "localhost"}:
            try:
                alt.append(x509.IPAddress(ipaddress.ip_address(n)))
            except ValueError:
                alt.append(x509.DNSName(n))
        cert = self._signed("Kastellan", key.public_key(), ExtendedKeyUsageOID.SERVER_AUTH, alt)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=self.ca_pem())
        with tempfile.TemporaryDirectory(prefix="kastellan-syslog-") as tmp:
            os.chmod(tmp, 0o700)
            chain, secret = Path(tmp) / "cert.pem", Path(tmp) / "key.pem"
            chain.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            fd = os.open(secret, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(_pem_key(key))
            ctx.load_cert_chain(chain, secret)
        return ctx


async def frames(reader: asyncio.StreamReader):
    """Messages as RFC 6587 sends them over TCP: octet-counted ("LEN MSG") or one per line."""
    while True:
        first = await reader.read(1)
        if not first:
            return
        if first.isdigit():
            digits = first + await reader.readuntil(b" ")
            length = int(digits[:-1])
            if length > MAX_MESSAGE:
                return
            yield await reader.readexactly(length)
        elif first in b"\r\n":
            continue
        else:
            yield (first + await reader.readuntil(b"\n"))[:MAX_MESSAGE].rstrip(b"\r\n")


class Listener:
    """The TLS syslog server; lines wait in `pending` for the writer."""

    def __init__(self, authority: Authority, known: Callable[[str], bool]) -> None:
        self.authority = authority
        self.known = known  # whether a device id is in config.yaml
        self.pending: list[logs.Line] = []
        self.received = 0
        self.refused = 0
        self.connections = 0
        self.active = True

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            device = self.authority.device_of(der)
            if device is None or not self.known(device) or self.connections >= MAX_CONNECTIONS:
                self.refused += 1
                return
            self.connections += 1
            try:
                async for message in frames(reader):
                    if not self.active or not self.known(device) or self.authority.device_of(der) != device:
                        self.refused += 1
                        return
                    line = logs.parse_syslog(message, device)
                    if line is None:
                        continue
                    line.verified = True
                    self.received += 1
                    self.pending.append(line)
                    if len(self.pending) > 5000:
                        del self.pending[:1000]
            finally:
                self.connections -= 1
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
            ssl.SSLError,
            ValueError,
        ):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 2)

    def take(self) -> list[logs.Line]:
        lines, self.pending = self.pending, []
        return lines
