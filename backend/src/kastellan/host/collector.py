"""The collector: how a Linux host is doing, read over SSH through one read-only door.

On the host (install_script, run once as root):
- a user `kastellan-collect` that can do nothing by itself;
- the script `scripts/kastellan-collect`, root-owned in /usr/local/lib/kastellan/, which reads
  and prints JSON (system, filesystems, RAID, SMART, temperatures, failed services, updates,
  Docker containers: lists only, never a container's environment);
- one sudo rule: that user may run exactly `python3 -I <the script>`, without arguments;
- Kastellan's collect key in its authorized_keys, bound to that command (`restrict`, `command=`,
  and `from=` the hub's address).

So whoever holds the hub gets this report from the host, and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
from importlib.resources import files
from typing import Any

import asyncssh

from ..access import authorize
from ..config import Device
from ..ssh import Keys, Target
from .report import normalize

USER = "kastellan-collect"

# Root runs this python3 (-I -S: no environment, no site packages, so no .pth file runs).
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
SCRIPT = "/usr/local/lib/kastellan/collect"
MAX_OUTPUT = 2 * 1024 * 1024


def script() -> str:
    return (files("kastellan.host") / "scripts" / "kastellan-collect").read_text()


def digest(text: str) -> str:
    """How a script installed from `text` (through a heredoc) reports itself: a hash of the file."""
    return hashlib.sha256((text.rstrip("\n") + "\n").encode()).hexdigest()[:16]


def outdated(report: dict[str, Any] | None) -> bool:
    """Whether the collector that sent this report is older than this hub's."""
    return report is not None and report.get("script") != digest(script())


def install_script(public_key: str, hub_ip: str | None, user: str = USER) -> str:
    """Shell commands, run as root, that set up the collector on a host."""
    q = shlex.quote
    end = "KASTELLAN_COLLECT_EOF"
    sudoers = f"/etc/sudoers.d/{user}"
    lines = [
        "set -eu",
        "umask 022",
        *PYTHON_CHECK.splitlines(),
        f"id -u {q(user)} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh {q(user)}",
        "# The script: root's; nobody else may change it.",
        "install -d -o root -g root -m 755 /usr/local/lib/kastellan",
        f"cat > {SCRIPT}.new <<'{end}'",
        script().rstrip("\n"),
        end,
        f"chown root:root {SCRIPT}.new && chmod 644 {SCRIPT}.new && mv {SCRIPT}.new {SCRIPT}",
        "# One sudo rule: exactly this command with exactly these arguments.",
        f'cmd="$py -I -S {SCRIPT}"',
        f'echo "{user} ALL=(root) NOPASSWD: $cmd" > {sudoers}.new',
        f"chmod 440 {sudoers}.new",
        f"if ! visudo -cf {sudoers}.new >/dev/null; then",
        f"  rm -f {sudoers}.new; echo 'bad sudo rule' >&2; exit 1",
        "fi",
        f"mv {sudoers}.new {sudoers}",
        "# Kastellan's collect key may run that, and nothing else (and only from the hub).",
        f"key={q(public_key)}",
        f"hub={q(hub_ip or '')}",
        'openssh_line="${hub:+from=\\"$hub\\",}restrict,command=\\"sudo -n $cmd\\" $key"',
        'limits="no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding"',
        'dropbear_line="command=\\"sudo -n $cmd\\",$limits $key"',
    ]
    # authorize() takes fixed lines; here they are the shell variables built above. An older key
    # line (another hub address, a rotated key) goes first.
    from .actor import keep_only_new_key

    for ln in authorize(user, "OPENSSH_LINE", "DROPBEAR_LINE"):
        lines.append(
            ln.replace("OPENSSH_LINE", '"$openssh_line"').replace("DROPBEAR_LINE", '"$dropbear_line"')
        )
    lines += keep_only_new_key()
    lines += [
        "command -v smartctl >/dev/null || echo 'Note: install smartmontools for disk health.' >&2",
        f"echo 'The collector is installed; Kastellan reads as {user}.'",
    ]
    return "\n".join(lines) + "\n"


def target_of(device: Device) -> Target:
    if device.collect is None:
        raise ValueError("the device has no collect: in config.yaml")
    port = device.collect.port or (device.ssh.port if device.ssh else 22)
    return Target(device.host, port, device.collect.user)


async def collect(keys: Keys, device: Device) -> dict[str, Any]:
    """The host's report, normalized (report.py: the host is not trusted). Read in pieces, and
    stopped past MAX_OUTPUT: a host can't make the hub hold more than that."""
    async with (
        await keys.connect(target_of(device), "collect") as conn,
        conn.create_process("collect", stdin=asyncssh.DEVNULL, encoding=None) as process,
    ):
        raw = bytearray()
        async with asyncio.timeout(90):
            while chunk := await process.stdout.read(65536):
                raw += chunk
                if len(raw) > MAX_OUTPUT:
                    process.kill()
                    raise RuntimeError("the collector's answer is too long")
            err = await process.stderr.read(4096)
            result = await process.wait(check=False)
    if result.exit_status != 0 or not raw:
        text = (err if isinstance(err, bytes) else b"").decode(errors="replace").strip()
        raise RuntimeError(
            text.splitlines()[-1][:200] if text else f"the collector answered nothing ({result.exit_status})"
        )
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as e:
        raise RuntimeError("not a collector report") from e
    if not isinstance(data, dict) or data.get("version") != 1:
        raise RuntimeError("not a collector report")
    return normalize(data)
