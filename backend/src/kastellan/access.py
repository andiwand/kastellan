"""Setting up SSH access to a device for Kastellan: the commands to run by hand, and running them
once with a login and password the admin types (used for that one connection, never stored).

Two ways, as the admin prefers:
- add Kastellan's key to an existing user (one login for everything);
- create a user just for Kastellan (needs root, or sudo), optionally with passwordless sudo
  (what "Install for me" in other places needs).

The key line carries `from="<the hub's address>"` when known, so a copy of the key is no use
from elsewhere.
"""

from __future__ import annotations

import re
import shlex
import socket

USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def hub_address(host: str, port: int = 22) -> str | None:
    """The hub's own address as the device will see it (the route to it); no packet is sent."""
    try:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.connect((host, port))
            return s.getsockname()[0]
    except OSError:
        return None


def key_line(public_key: str, hub_ip: str | None) -> str:
    return f'from="{hub_ip}" {public_key}' if hub_ip else public_key


def setup_script(user: str, public_key: str, hub_ip: str | None, create: bool, sudo: bool) -> str:
    """Shell commands that let Kastellan log in as `user`: create it if asked, add the key (to
    dropbear's file for root on OpenWrt), and allow passwordless sudo if asked. Run as root when
    creating a user or allowing sudo; else as that user."""
    if not USER.match(user):
        raise ValueError("user names are lowercase letters, digits, _ and - (up to 32)")
    q = shlex.quote
    line = key_line(public_key, hub_ip)
    lines = ["set -eu"]
    if create:
        lines += [
            "# A user just for Kastellan.",
            f"id -u {user} >/dev/null 2>&1 || useradd --create-home --shell /bin/sh {user}",
        ]
    lines += [
        "# Kastellan's key (it can log in from the hub's address only).",
        f"if [ {q(user)} = root ] && [ -d /etc/dropbear ]; then keys=/etc/dropbear/authorized_keys",
        f'else home=$(getent passwd {user} | cut -d: -f6); mkdir -p "$home/.ssh"; chmod 700 "$home/.ssh"; '
        'keys="$home/.ssh/authorized_keys"; fi',
        f"line={q(line)}",
        'grep -qxF "$line" "$keys" 2>/dev/null || echo "$line" >> "$keys"',
        'chmod 600 "$keys"',
        f'[ {q(user)} = root ] || chown {user}: "$keys" "$(dirname "$keys")"',
    ]
    if sudo:
        lines += [
            "# Passwordless sudo, so Kastellan can install things it is asked to (audited there).",
            f"echo {q(f'{user} ALL=(ALL) NOPASSWD: ALL')} > /etc/sudoers.d/kastellan-{user}",
            f"chmod 440 /etc/sudoers.d/kastellan-{user}",
            f"visudo -cf /etc/sudoers.d/kastellan-{user} >/dev/null",
        ]
    lines.append(f"echo 'Kastellan can log in as {user}.'")
    return "\n".join(lines) + "\n"
