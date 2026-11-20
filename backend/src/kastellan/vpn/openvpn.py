"""OpenVPN clients through an easy-rsa PKI, driven by two small scripts (tools/):

- `kastellan-pki` on the PKI host (e.g. SV1): list, issue, revoke, crl, export. The hub's key is
  bound to it in authorized_keys (`command=`), so the hub can do these and nothing else there;
  the CA key never leaves that host.
- `kastellan-crl` on the OpenVPN server (e.g. OpenWrt): takes a new CRL and puts it in place.

Both are reached over the hub's SSH with pinned host keys (ssh.py), like the terminal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ..ssh import Keys, Target

NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class OvpnClient:
    name: str
    status: str  # valid, revoked, expired
    expires: str | None  # ISO date
    serial: str


def parse_list(text: str, now: datetime | None = None) -> list[OvpnClient]:
    """kastellan-pki list: status, expiry (YYMMDDHHMMSSZ), serial, name per line."""
    now = now or datetime.now(UTC)
    out: list[OvpnClient] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 4 or not NAME.match(parts[3]):
            continue
        flag, expiry, serial, name = parts
        try:
            when = datetime.strptime(expiry, "%y%m%d%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            when = None
        status = {"V": "valid", "R": "revoked", "E": "expired"}.get(flag, "unknown")
        if status == "valid" and when and when < now:
            status = "expired"
        out.append(OvpnClient(name, status, when.date().isoformat() if when else None, serial[:64]))
    return out


class OpenVpn:
    def __init__(self, keys: Keys, pki: Target, server: Target | None) -> None:
        self.keys, self.pki, self.server = keys, pki, server

    async def _run(self, target: Target, command: str, input: str | None = None) -> str:
        async with await self.keys.connect(target) as conn:
            result = await conn.run(command, input=input, check=False, timeout=120)
        if result.exit_status != 0:
            message = str(result.stderr or "").strip().splitlines()
            raise RuntimeError(message[-1] if message else f"{command.split()[0]} failed")
        return str(result.stdout or "")

    async def clients(self) -> list[OvpnClient]:
        return parse_list(await self._run(self.pki, "list"))

    async def issue(self, name: str, days: int) -> None:
        _check(name)
        if not 1 <= days <= 3650:
            raise ValueError("days must be 1 to 3650")
        await self._run(self.pki, f"issue {name} {days}")

    async def revoke(self, name: str) -> None:
        """Revoke on the PKI host (and renew the CRL there). Then push_crl()."""
        _check(name)
        await self._run(self.pki, f"revoke {name}")

    async def push_crl(self, renew: bool = False) -> str:
        """Bring the PKI host's CRL to the OpenVPN server; with renew, renew it first (CRLs
        expire, and an expired one makes OpenVPN refuse every client). Returns the CRL (PEM)."""
        if renew:
            await self._run(self.pki, "renew-crl")
        crl = await self._run(self.pki, "crl")
        if self.server is None:
            raise RuntimeError("no vpn.openvpn.server: the CRL can't reach the server")
        await self._run(self.server, "install", input=crl)
        return crl

    async def export(self, name: str) -> str:
        _check(name)
        return await self._run(self.pki, f"export {name}")


RESERVED = {"ca", "server"}  # never clients (kastellan-pki refuses them too)


def _check(name: str) -> None:
    if not NAME.match(name):
        raise ValueError("names are lowercase letters, digits, . _ - (up to 64)")
    if name in RESERVED:
        raise ValueError(f"{name} is reserved")


def crl_next_update(pem: str) -> str | None:
    """When a CRL expires (ISO date), or None if it can't be read."""
    from cryptography import x509

    try:
        crl = x509.load_pem_x509_crl(pem.encode())
    except ValueError:
        return None
    when = crl.next_update_utc
    return when.isoformat(timespec="minutes") if when else None
