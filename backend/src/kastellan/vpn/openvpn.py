"""OpenVPN clients through an easy-rsa PKI, driven by two small scripts (scripts/):

- `kastellan-pki` on the PKI host (e.g. SV1): list, issue, revoke, renew-crl, crl, export. It only
  ever touches client certificates; the CA key and the server's key can't be reached through it.
- `kastellan-crl` on the OpenVPN server (e.g. OpenWrt): checks a new CRL and puts it in place.

Two ways to run them (`vpn.openvpn.mode`):

- restricted: installed on the hosts; the hub's VPN key is bound to them with `command=` in
  authorized_keys, so it can do these things and nothing else there. The safest way.
- direct: nothing installed; on every call the hub sends the same script over the device's normal
  `ssh:` login and runs it once. Simpler to set up, but that login can do anything.

`install_script()` writes the shell commands that set up restricted mode on a host; the app shows
them for copying, and can run them itself over the device's normal login (as root or with
passwordless sudo).
"""

from __future__ import annotations

import re
import secrets
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files

import asyncssh

from ..access import authorize, openssh_options
from ..config import Device, OpenVpnSource
from ..ssh import Keys, Target

NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
SCRIPTS = files("kastellan.vpn") / "scripts"


def script(name: str) -> str:
    return (SCRIPTS / name).read_text()


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


def pki_conf(s: OpenVpnSource, restricted: bool = False) -> str:
    """The script's settings. Installed (restricted), the TLS key is the copy its user can read."""
    q = shlex.quote
    tls = (TLS_COPY if restricted else s.tls_key) if s.tls_key else ""
    return (
        f"EASYRSA_DIR={q(s.easyrsa_dir)}\n"
        f"TEMPLATE={q(s.template_path)}\n"
        f"TLS_KEY={q(tls)}\n"
        f"RESERVED={q(' '.join(s.reserved))}\n"
    )


def crl_conf(s: OpenVpnSource) -> str:
    return f"CRL={shlex.quote(s.crl_path)}\nCA={shlex.quote(s.ca_path)}\n"


def _heredoc(path: str, text: str) -> str:
    """A shell line writing text to path; the delimiter is random, so the text can't end it."""
    end = "KASTELLAN_" + secrets.token_hex(8)
    return f"cat > {path} <<'{end}'\n{text.rstrip(chr(10))}\n{end}\n"


def direct_command(script_name: str, conf: str, command: str) -> str:
    """Run a script once over a normal login: write it and its config to a private temp folder,
    run it with the requested command, remove it. The command goes in as the script expects it
    (SSH_ORIGINAL_COMMAND), set by this shell line, not by the SSH client's environment."""
    shell = "bash" if script_name == "kastellan-pki" else "sh"
    return (
        "set -e; umask 077; d=$(mktemp -d); trap 'rm -rf \"$d\"' EXIT\n"
        + _heredoc('"$d/run"', script(script_name))
        + _heredoc('"$d/conf"', conf)
        + f'SSH_ORIGINAL_COMMAND={shlex.quote(command)} {shell} "$d/run" "$d/conf"\n'
    )


PKI_COMMAND = 'command="/usr/local/bin/kastellan-pki /etc/kastellan-pki.conf"'
CRL_COMMAND = 'command="/usr/bin/kastellan-crl /etc/kastellan-crl.conf"'
# dropbear (OpenWrt) knows neither `restrict` nor `from=`; these are what it has.
DROPBEAR_LIMITS = "no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding"
TLS_COPY = "/etc/kastellan-pki/tls.key"


def key_lines(command: str, key: str, hub_ip: str | None) -> tuple[str, str]:
    """(OpenSSH line, dropbear line) binding the VPN key to one command."""
    return (
        openssh_options(hub_ip, "restrict", command) + key,
        f"{command},{DROPBEAR_LIMITS} {key}",
    )


def install_script(role: str, s: OpenVpnSource, vpn_public_key: str, hub_ip: str | None = None) -> str:
    """Shell commands (run as root) that set up restricted mode on the PKI host ("pki") or the
    OpenVPN server ("server"). Shown for copying; also what "Install for me" runs."""
    q = shlex.quote
    lines = ["set -eu", "umask 022"]
    if role == "pki":
        u = s.pki_user
        pki = f"{s.easyrsa_dir.rstrip('/')}/pki"
        lines += [
            f"# {u} owns the PKI's data (pki/: the CA key); the easyrsa program stays root's.",
            f"id -u {u} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh {u}",
            f"if command -v sudo >/dev/null && sudo -l -U {u} 2>/dev/null | grep -q 'may run'; then",
            f"  echo 'Warning: {u} may use sudo; give the PKI a user that can do nothing else.' >&2",
            "fi",
            f"chown -R {u}: {q(pki)} && chmod 700 {q(pki)}",
            f"grep -rqs {q('crl-verify ' + pki)} /etc/openvpn && echo 'Note: an OpenVPN here reads the CRL "
            f"inside {pki}, which only {u} can read now; point crl-verify at a copy.' >&2 || true",
            "# The script, and its settings.",
            _heredoc("/usr/local/bin/kastellan-pki", script("kastellan-pki")).rstrip("\n"),
            "chmod 755 /usr/local/bin/kastellan-pki",
            "mkdir -p /etc/kastellan-pki",
            _heredoc("/etc/kastellan-pki.conf", pki_conf(s, restricted=True)).rstrip("\n"),
            "chmod 644 /etc/kastellan-pki.conf",
        ]
        if s.tls_key:
            lines += [
                f"# A copy of the TLS key {u} can read (run this again if that key changes).",
                f"cp {q(s.tls_key)} {TLS_COPY} && chown {u}: {TLS_COPY} && chmod 600 {TLS_COPY}",
            ]
        if s.client_template:
            lines += [
                f"mkdir -p {q(s.template_path.rsplit('/', 1)[0])}",
                _heredoc(q(s.template_path), s.client_template).rstrip("\n"),
                f"chmod 644 {q(s.template_path)}",
            ]
        lines.append("# Kastellan's VPN key may run this script, and nothing else.")
        lines += authorize(u, *key_lines(PKI_COMMAND, vpn_public_key, hub_ip))
        lines.append("echo kastellan-pki installed")
    else:
        u = s.server_user
        lines += [
            "# openssl lets the script check each CRL's signature (OpenWrt: openssl-util).",
            "command -v openssl >/dev/null || (command -v opkg >/dev/null && opkg install openssl-util) \\",
            "  || true",
            _heredoc("/usr/bin/kastellan-crl", script("kastellan-crl")).rstrip("\n"),
            "chmod 755 /usr/bin/kastellan-crl",
            _heredoc("/etc/kastellan-crl.conf", crl_conf(s)).rstrip("\n"),
            "chmod 644 /etc/kastellan-crl.conf",
        ]
        if u != "root":
            folder = s.crl_path.rsplit("/", 1)[0] or "/"
            lines += [
                f"id -u {u} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh {u}",
                f"# {u} replaces the CRL: it needs to write where it is.",
                f"echo 'Note: {u} must be able to write {folder}' >&2",
            ]
        lines.append("# Kastellan's VPN key may run this script, and nothing else.")
        lines += authorize(u, *key_lines(CRL_COMMAND, vpn_public_key, hub_ip))
        lines += [
            f"grep -rqs {q('crl-verify ' + s.crl_path)} /etc/openvpn /etc/config/openvpn "
            f"|| echo 'Note: add \"crl-verify {s.crl_path}\" to the OpenVPN server config.'",
            "echo kastellan-crl installed",
        ]
    return "\n".join(lines) + "\n"


class OpenVpn:
    def __init__(self, keys: Keys, settings: OpenVpnSource, pki: Device, server: Device | None) -> None:
        self.keys, self.s = keys, settings
        self.pki_device, self.server_device = pki, server

    def _target(self, device: Device, restricted_user: str) -> tuple[Target, str]:
        """Where to log in, and with which key."""
        port = device.ssh.port if device.ssh else 22
        if self.s.mode == "restricted":
            return Target(device.host, port, restricted_user), "vpn"
        assert device.ssh is not None  # checked by the config
        return Target(device.host, port, device.ssh.user), "hub"

    async def _run(
        self, device: Device, user: str, name: str, conf: str, command: str, input: str | None = None
    ) -> str:
        target, which = self._target(device, user)
        remote = command if self.s.mode == "restricted" else direct_command(name, conf, command)
        async with await self.keys.connect(target, which) as conn:
            stdin = {"input": input} if input else {"stdin": asyncssh.DEVNULL}
            result = await conn.run(remote, check=False, timeout=120, **stdin)
        if result.exit_status != 0:
            message = str(result.stderr or "").strip().splitlines()
            raise RuntimeError(message[-1] if message else f"{command.split()[0]} failed")
        return str(result.stdout or "")

    async def _pki(self, command: str) -> str:
        return await self._run(self.pki_device, self.s.pki_user, "kastellan-pki", pki_conf(self.s), command)

    async def clients(self) -> list[OvpnClient]:
        return parse_list(await self._pki("list"))

    async def issue(self, name: str, days: int) -> None:
        self._check(name)
        if not 1 <= days <= 3650:
            raise ValueError("days must be 1 to 3650")
        await self._pki(f"issue {name} {days}")

    async def revoke(self, name: str) -> None:
        """Revoke on the PKI host (and renew the CRL there). Then push_crl()."""
        self._check(name)
        await self._pki(f"revoke {name}")

    async def push_crl(self, renew: bool = False) -> str:
        """Bring the PKI host's CRL to the OpenVPN server; with renew, renew it first (CRLs
        expire, and an expired one makes OpenVPN refuse every client). Returns the CRL (PEM)."""
        if renew:
            await self._pki("renew-crl")
        crl = await self._pki("crl")
        if self.server_device is None:
            raise RuntimeError("no vpn.openvpn.server: the CRL can't reach the server")
        await self._run(
            self.server_device, self.s.server_user, "kastellan-crl", crl_conf(self.s), "install", crl
        )
        return crl

    async def export(self, name: str) -> str:
        self._check(name)
        return await self._pki(f"export {name}")

    def _check(self, name: str) -> None:
        if not NAME.match(name):
            raise ValueError("names are lowercase letters, digits, . _ - (up to 64)")
        if name in {"ca", *self.s.reserved}:
            raise ValueError(f"{name} is reserved")


def crl_next_update(pem: str) -> str | None:
    """When a CRL expires (ISO time), or None if it can't be read."""
    from cryptography import x509

    try:
        crl = x509.load_pem_x509_crl(pem.encode())
    except ValueError:
        return None
    when = crl.next_update_utc
    return when.isoformat(timespec="minutes") if when else None
