"""Setting up SSH access to a device for Kastellan: the commands to run by hand, and running them
once with a login and password the admin types (used for that one connection, never stored).

Two ways, as the admin prefers:
- add Kastellan's key to an existing user (one login for everything);
- create a user just for Kastellan (needs root, or sudo), with passwordless sudo (what "Install
  for me" in other places needs).

`authorize()` is the one place keys are added (here and for the VPN scripts):
- the key is written *as the target user* (su), never by root into a home the user controls,
  where a symlink could point root's write at /etc/shadow;
- OpenWrt with dropbear (its default; `DROPBEAR`): root's keys are in /etc/dropbear; dropbear
  doesn't know `from=` or `restrict`, so its line is the plain form;
- OpenSSH (elsewhere, and OpenWrt with openssh-server running instead): the line carries
  `from="<the hub's address>"`, so a copy of the key is no use from anywhere else.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shlex
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ActAccess, CollectAccess, Device

# What config.yaml allows for a login (SshAccess.user), never starting with "-". Checked with
# valid_user or check_user (fullmatch: "root\n" is no name).
USER = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._-]{0,31}")  # with fullmatch (valid_user)
USER_RULE = "user names are letters, digits, . _ - (up to 32, not starting with -)"

# Root runs this python3 (-I -S: no environment, no site packages, so no .pth file runs); shell
# lines that set $py to it, for the collector's and the actor's install scripts.
PYTHON_CHECK = r"""py=/usr/bin/python3
[ -x "$py" ] || py=$(command -v python3) || { echo "needs python3" >&2; exit 1; }
# Root will run this interpreter: the file, and the folders it is found in, must be root's
# alone (a link's own rights mean nothing; what it points to counts).
real=$(readlink -f "$py")
for p in "$real" "$(dirname "$real")" "$(dirname "$py")"; do
  if [ "$(stat -c %u "$p")" != 0 ] || [ -n "$(find "$p" -maxdepth 0 -perm /022)" ]; then
    echo "$p isn't root's alone (owner or write rights): not using it" >&2; exit 1
  fi
done"""

# True in sh on an OpenWrt device whose SSH server is dropbear (its default). One with
# openssh-server running instead reads ~/.ssh/authorized_keys, also root's, and knows `from=`.
DROPBEAR = "[ -f /etc/openwrt_release ] && ! pidof sshd >/dev/null 2>&1"

# Run as the target user: $0 is the authorized_keys file, $1 the line.
_ADD = (
    'umask 077; d=$(dirname "$0"); mkdir -p "$d"; chmod 700 "$d"; '
    'grep -qxF "$1" "$0" 2>/dev/null || printf "%s\\n" "$1" >> "$0"; chmod 600 "$0"'
)

# Run as the target user: $0 is the authorized_keys file, $1 the line just added, which stays
# the only one (only if it is there: an interruption never leaves the file without it).
_ONLY = 'grep -qxF -- "$1" "$0" && grep -m1 -xF -- "$1" "$0" > "$0.k" && cat "$0.k" > "$0"; rm -f "$0.k"'

# Run as the file's owner: $0 the file, $1 the line about to be added, $2 a script path, $3 a
# key's data. Removes the lines that bind that key to that script with other options (a VPN set
# up again on the same login: the first matching line counts, so an old one would win).
_DEDUPE = (
    '[ -f "$0" ] || exit 0; '
    'awk -v s="command=\\"$2" -v k=" $3" -v l="$1" \''
    '{ i = index($0, s); c = i ? substr($0, i + length(s), 1) : ""; '
    'if (i && (c == " " || c == "\\"") && index($0, k) && $0 != l) next; print }'
    '\' "$0" > "$0.k" && cat "$0.k" > "$0"; rm -f "$0.k"'
)


def hub_address(host: str, port: int = 22) -> str | None:
    """The hub's own address as the device will see it (the route to it); no packet is sent."""
    try:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return None


async def ssh_greeting(host: str, port: int = 22, wait: float = 3.0) -> str:
    """The line an SSH server greets with (SSH-2.0-dropbear_2022.83), or "" (no login)."""
    with contextlib.suppress(OSError, TimeoutError, ValueError):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), wait)
        try:
            return (await asyncio.wait_for(reader.readline(), wait)).decode("ascii", "replace").strip()
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
    return ""


def valid_user(user: str) -> bool:
    return USER.fullmatch(user) is not None


def check_user(user: str) -> str:
    if not valid_user(user):
        raise ValueError(USER_RULE)
    return user


def port_of(device: Device, login: ActAccess | CollectAccess | None = None) -> int:
    """The SSH port for one of Kastellan's logins on the device (act:, collect:): its own, else
    the ssh: login's, else 22."""
    return (login.port if login else None) or (device.ssh.port if device.ssh else 22)


def openssh_options(hub_ip: str | None, *more: str) -> str:
    """OpenSSH key options; empty, or ending in a space."""
    options = ([f'from="{hub_ip}"'] if hub_ip else []) + list(more)
    return ",".join(options) + " " if options else ""


def authorize(
    user: str,
    openssh_line: str,
    dropbear_line: str,
    replace: tuple[str, str] | None = None,
    only: bool = False,
) -> list[str]:
    """Shell lines adding a key for `user` (run as root, or as that user). With `replace`
    (script path, key data): lines binding that key to that script otherwise go first. With
    `only` (a user of Kastellan's own): the file keeps only this line, so an older key or hub
    address doesn't stay valid."""
    q = shlex.quote
    script, data = replace or ("", "")
    none = chr(39) * 2
    return [
        f"user={q(check_user(user))}",
        f"script={q(script)}; keydata={q(data)}; dedupe={q(_DEDUPE) if replace else none}",
        f"only={q(_ONLY) if only else none}",
        f"if {DROPBEAR}; then dropbear=1; else dropbear=; fi",
        'if [ -n "$dropbear" ]; then line=' + q(dropbear_line) + "; else line=" + q(openssh_line) + "; fi",
        'if [ -n "$dropbear" ] && [ "$user" = root ]; then',
        "  keys=/etc/dropbear/authorized_keys",
        '  [ -z "$dedupe" ] || sh -c "$dedupe" "$keys" "$line" "$script" "$keydata"',
        '  grep -qxF "$line" "$keys" 2>/dev/null || printf \'%s\\n\' "$line" >> "$keys"',
        '  chmod 600 "$keys"',
        "else",
        '  home=$(getent passwd "$user" 2>/dev/null | cut -d: -f6)',
        '  [ -n "$home" ] || home=$(awk -F: -v u="$user" \'$1 == u { print $6 }\' /etc/passwd)',
        '  if [ -z "$home" ] || [ ! -d "$home" ]; then',
        '    echo "no user $user (or no home folder)" >&2; exit 1',
        "  fi",
        "  # Written as $user: a symlink in their home can't send root's write anywhere.",
        f"  add={q(_ADD)}",
        '  f="$home/.ssh/authorized_keys"',
        '  if [ "$(id -un)" = "$user" ]; then',
        '    [ -z "$dedupe" ] || sh -c "$dedupe" "$f" "$line" "$script" "$keydata"',
        '    sh -c "$add" "$f" "$line"',
        '    [ -z "$only" ] || sh -c "$only" "$f" "$line"',
        "  else",
        '    [ -z "$dedupe" ] || su -s /bin/sh "$user" -c "$dedupe" "$f" "$line" "$script" "$keydata"',
        '    su -s /bin/sh "$user" -c "$add" "$f" "$line"',
        '    [ -z "$only" ] || su -s /bin/sh "$user" -c "$only" "$f" "$line"',
        "  fi",
        "fi",
    ]


def setup_script(user: str, public_key: str, hub_ip: str | None, create: bool, sudo: bool) -> str:
    """Shell commands that let Kastellan log in as `user`: create it if asked, add the key, and
    allow passwordless sudo if asked. Run as root when creating a user, allowing sudo or adding
    the key for someone else; else as that user."""
    q = shlex.quote
    check_user(user)
    lines = ["set -eu"]
    if create:
        lines += [
            "# A user just for Kastellan.",
            f"id -u {q(user)} >/dev/null 2>&1 || useradd --create-home --shell /bin/sh {q(user)}",
        ]
    lines.append("# Kastellan's key (on OpenSSH only from the hub's address).")
    lines += authorize(user, openssh_options(hub_ip) + public_key, public_key)
    if sudo:
        # sudo skips files in sudoers.d with a "." in their name.
        rules = "/etc/sudoers.d/kastellan-" + user.replace(".", "_")
        lines += [
            "# Passwordless sudo, so Kastellan can install what it is asked to (audited there).",
            f"echo {q(f'{user} ALL=(ALL) NOPASSWD: ALL')} > {rules}.new",
            f"chmod 440 {rules}.new",
            f"if ! visudo -cf {rules}.new >/dev/null; then",
            f"  rm -f {rules}.new; echo 'bad sudo rule' >&2; exit 1",
            "fi",
            f"mv {rules}.new {rules}",
        ]
    lines.append(f"echo {q(f'Kastellan can log in as {user}.')}")
    return "\n".join(lines) + "\n"
