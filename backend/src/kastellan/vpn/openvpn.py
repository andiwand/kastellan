"""OpenVPN clients through an easy-rsa PKI, driven by two small scripts (scripts/):

- `kastellan-pki` on the PKI host (e.g. SV1): list, issue, revoke, renew-crl, crl, export. It only
  ever touches client certificates; the CA key and the server's key can't be reached through it.
- `kastellan-crl` on the OpenVPN server (e.g. OpenWrt): checks a new CRL and puts it in place.

Two ways to run them (a VPN's `mode` under `vpns:`):

- restricted: installed on the hosts; the hub's VPN key is bound to them with `command=` in
  authorized_keys, so it can do these things and nothing else there. The safest way.
- direct: nothing installed; on every call the hub sends the same script over the device's normal
  `ssh:` login and runs it once. Simpler to set up, but that login can do anything.

`install_script()` writes the shell commands that set up restricted mode on a host; the app shows
them for copying, and can run them itself over the device's normal login (as root or with
passwordless sudo).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

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


@dataclass(frozen=True)
class Connection:
    name: str  # the client certificate's common name
    address: str | None  # where it connects from (the real address)
    since: str | None  # ISO time
    rx: int | None  # bytes the server received from it
    tx: int | None


def _when(text: str) -> str | None:
    """A status file's time: seconds (version 2/3) or a date (version 1; the router's clock, taken
    as UTC)."""
    text = text.strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text), UTC).isoformat(timespec="seconds")
    for form in ("%a %b %d %H:%M:%S %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return (
                datetime.strptime(" ".join(text.split()), form)
                .replace(tzinfo=UTC)
                .isoformat(timespec="seconds")
            )
        except ValueError:
            pass
    return None


def split_status(text: str) -> tuple[dict[str, str], bool]:
    """The openvpn-status script's output by server instance (`== <uci section or file>`), and
    whether it found no server at all."""
    parts: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines()[:20000]:
        if line.startswith("== "):
            current = parts.setdefault(line[3:].strip()[:64], [])
        elif current is not None:
            current.append(line)
    return {k: "\n".join(v) for k, v in parts.items()}, "!no-servers" in text.splitlines()


def parse_status(text: str) -> list[Connection]:
    """The client lines of OpenVPN status files (status-version 1, 2 or 3), as the device's
    openvpn-status script prints them; untrusted, so anything odd is dropped."""
    out: dict[str, Connection] = {}
    header: list[str] = []
    v1 = False

    def number(value: str | None) -> int | None:
        return int(value) if value and value.isdigit() and len(value) < 20 else None

    for line in text.splitlines()[:5000]:
        sep = "\t" if "\t" in line else ","
        fields = line.split(sep)
        if line.startswith("== ") or line.startswith("!"):
            v1 = False
            continue
        if fields[0] == "HEADER" and fields[1:2] == ["CLIENT_LIST"]:
            header = fields[1:]
            continue
        if line.startswith("Common Name,Real Address"):
            v1 = True
            continue
        if fields[0] == "CLIENT_LIST":
            col = {name: i for i, name in enumerate(header)}
            pick = {
                k: fields[i] if (i := col.get(k, default)) < len(fields) else ""
                for k, default in (
                    ("Common Name", 1),
                    ("Real Address", 2),
                    ("Bytes Received", 5),
                    ("Bytes Sent", 6),
                    ("Connected Since", 7),
                    ("Connected Since (time_t)", 8),
                )
            }
            name, address = pick["Common Name"], pick["Real Address"]
            since = _when(pick["Connected Since (time_t)"]) or _when(pick["Connected Since"])
            rx, tx = number(pick["Bytes Received"]), number(pick["Bytes Sent"])
        elif v1 and len(fields) >= 5:
            name, address, rx, tx = fields[0], fields[1], number(fields[2]), number(fields[3])
            since = _when(fields[4])
        else:
            continue
        if not name or not NAME.match(name):  # UNDEF (not through yet), or not a client name
            continue
        out[name] = Connection(name, (address or "")[:64] or None, since, rx, tx)
    return list(out.values())


class Seen:
    """When each OpenVPN client was last seen connected (status files only list who is connected
    now): <data>/vpn/openvpn-seen[-<vpn>].json, name -> ISO time; one per OpenVPN VPN."""

    def __init__(self, folder: Path, vid: str = "openvpn") -> None:
        # "openvpn": the one from before there could be several keeps its file.
        self.path = folder / ("openvpn-seen.json" if vid == "openvpn" else f"openvpn-seen-{vid}.json")
        try:
            data = json.loads(self.path.read_text())
            self.last = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.last = {}

    def note(self, connected: list[Connection], now: datetime | None = None) -> None:
        if not connected:
            return
        stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
        self.last.update({c.name: stamp for c in connected})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.last, indent=1))
        os.replace(temp, self.path)


def pki_conf(s: OpenVpnSource, restricted: bool = False, vid: str = "openvpn") -> str:
    """The script's settings. Installed (restricted), the TLS key is the copy its user can read."""
    q = shlex.quote
    tls = (tls_copy(vid) if restricted else s.tls_key) if s.tls_key else ""
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


# dropbear (OpenWrt) knows neither `restrict` nor `from=`; these are what it has.
DROPBEAR_LIMITS = "no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding"


def _own(vid: str, path: str, ext: str) -> str:
    """A VPN's own file on a host (several VPNs can share one); "openvpn", the one from before
    there could be several, keeps the plain name."""
    return f"{path}{ext}" if vid == "openvpn" else f"{path}-{vid}{ext}"


def pki_conf_path(vid: str) -> str:
    return _own(vid, "/etc/kastellan-pki", ".conf")


def crl_conf_path(vid: str) -> str:
    return _own(vid, "/etc/kastellan-crl", ".conf")


def tls_copy(vid: str) -> str:
    return _own(vid, "/etc/kastellan-pki/tls", ".key")


def pki_command(vid: str) -> str:
    return f'command="/usr/local/bin/kastellan-pki {pki_conf_path(vid)}"'


def crl_command(vid: str) -> str:
    return f'command="/usr/bin/kastellan-crl {crl_conf_path(vid)}"'


def key_lines(command: str, key: str, hub_ip: str | None) -> tuple[str, str]:
    """(OpenSSH line, dropbear line) binding the VPN key to one command."""
    return (
        openssh_options(hub_ip, "restrict", command) + key,
        f"{command},{DROPBEAR_LIMITS} {key}",
    )


def install_script(
    role: str, s: OpenVpnSource, vpn_public_key: str, hub_ip: str | None = None, vid: str = "openvpn"
) -> str:
    """Shell commands (run as root) that set up restricted mode for VPN `vid` on its PKI host
    ("pki") or its OpenVPN server ("server"). Shown for copying; also what "Install for me" runs."""
    q = shlex.quote
    pki_path, crl_path, tls = pki_conf_path(vid), crl_conf_path(vid), tls_copy(vid)
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
            _heredoc(pki_path, pki_conf(s, restricted=True, vid=vid)).rstrip("\n"),
            f"chmod 644 {pki_path}",
        ]
        if s.tls_key:
            lines += [
                f"# A copy of the TLS key {u} can read (run this again if that key changes).",
                f"cp {q(s.tls_key)} {tls} && chown {u}: {tls} && chmod 600 {tls}",
            ]
        if s.client_template:
            lines += [
                f"mkdir -p {q(s.template_path.rsplit('/', 1)[0])}",
                _heredoc(q(s.template_path), s.client_template).rstrip("\n"),
                f"chmod 644 {q(s.template_path)}",
            ]
        lines.append("# Kastellan's VPN key may run this script, and nothing else.")
        lines += authorize(
            u,
            *key_lines(pki_command(vid), vpn_public_key, hub_ip),
            replace=("/usr/local/bin/kastellan-pki", vpn_public_key.split()[1]),
        )
        lines.append("echo kastellan-pki installed")
    else:
        u = s.server_user
        lines += [
            "# openssl lets the script check each CRL's signature (OpenWrt: openssl-util).",
            "command -v openssl >/dev/null || (command -v opkg >/dev/null && opkg install openssl-util) \\",
            "  || true",
            _heredoc("/usr/bin/kastellan-crl", script("kastellan-crl")).rstrip("\n"),
            "chmod 755 /usr/bin/kastellan-crl",
            _heredoc(crl_path, crl_conf(s)).rstrip("\n"),
            f"chmod 644 {crl_path}",
        ]
        if u != "root":
            folder = s.crl_path.rsplit("/", 1)[0] or "/"
            lines += [
                f"id -u {u} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh {u}",
                f"# {u} replaces the CRL: it needs to write where it is.",
                f"echo 'Note: {u} must be able to write {folder}' >&2",
            ]
        lines.append("# Kastellan's VPN key may run this script, and nothing else.")
        lines += authorize(
            u,
            *key_lines(crl_command(vid), vpn_public_key, hub_ip),
            replace=("/usr/bin/kastellan-crl", vpn_public_key.split()[1]),
        )
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
            if not input:
                result = await conn.run(remote, check=False, timeout=120, stdin=asyncssh.DEVNULL)
            else:
                # The script may refuse and exit before reading its input: the write then fails,
                # but its exit status and message (stderr) are what tell why.
                async with conn.create_process(remote) as process:
                    try:
                        process.stdin.write(input)
                        process.stdin.write_eof()
                    except (BrokenPipeError, ConnectionError, asyncssh.Error):
                        pass
                    result = await process.wait(check=False, timeout=120)
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
            raise RuntimeError("no server: for this VPN: the CRL can't reach the server")
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
