"""Setting up SSH access to a device for Kastellan: the commands to run by hand, and running them
once with a login and password the admin types (used for that one connection, never stored).

Two ways, as the admin prefers:
- add Kastellan's key to an existing user (one login for everything);
- create a user just for Kastellan (needs root, or sudo), with passwordless sudo (what "Install
  for me" in other places needs).

`authorize()` is the one place keys are added (here and for the VPN scripts):
- the key is written *as the target user* (su), never by root into a home the user controls,
  where a symlink could point root's write at /etc/shadow;
- OpenWrt (dropbear): root's keys are in /etc/dropbear; dropbear doesn't know `from=` or
  `restrict`, so its line is the plain form;
- elsewhere (OpenSSH): the line carries `from="<the hub's address>"`, so a copy of the key is no
  use from anywhere else.
"""

from __future__ import annotations

import re
import shlex
import socket

# What config.yaml allows for a login (SshAccess.user), never starting with "-".
USER = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._-]{0,31}$")

# Run as the target user: $0 is the authorized_keys file, $1 the line.
_ADD = (
    'umask 077; d=$(dirname "$0"); mkdir -p "$d"; chmod 700 "$d"; '
    'grep -qxF "$1" "$0" 2>/dev/null || printf "%s\\n" "$1" >> "$0"; chmod 600 "$0"'
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


def check_user(user: str) -> str:
    if not USER.match(user):
        raise ValueError("user names are letters, digits, . _ - (up to 32, not starting with -)")
    return user


def openssh_options(hub_ip: str | None, *more: str) -> str:
    """OpenSSH key options; empty, or ending in a space."""
    options = ([f'from="{hub_ip}"'] if hub_ip else []) + list(more)
    return ",".join(options) + " " if options else ""


def authorize(user: str, openssh_line: str, dropbear_line: str) -> list[str]:
    """Shell lines adding a key for `user` (run as root, or as that user)."""
    q = shlex.quote
    return [
        f"user={q(check_user(user))}",
        "if [ -f /etc/openwrt_release ]; then line="
        + q(dropbear_line)
        + "; else line="
        + q(openssh_line)
        + "; fi",
        'if [ -f /etc/openwrt_release ] && [ "$user" = root ]; then',
        "  keys=/etc/dropbear/authorized_keys",
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
        '  if [ "$(id -un)" = "$user" ]; then sh -c "$add" "$home/.ssh/authorized_keys" "$line"',
        '  else su -s /bin/sh "$user" -c "$add" "$home/.ssh/authorized_keys" "$line"; fi',
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
