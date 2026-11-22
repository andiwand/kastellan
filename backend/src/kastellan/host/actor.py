"""The actor: what Kastellan may change on a Linux host, ability by ability, each installed only
when chosen.

On the host (install_script, run as root; running it again with another choice changes it to
exactly that choice):
- a user `kastellan-act` that can do nothing by itself;
- a dispatcher (scripts/act-dispatch), the only thing the act key may run (`restrict`,
  `command=`, `from=` the hub): it accepts exactly the known forms (`update-container <name>
  [<tag>]`, `vm-start <name>`, `journal <cursor>`, `reboot`, ...) and `abilities` (which lists
  what is installed), and starts the ability;
- per chosen ability, a root-owned script (act-common + act-<ability>) and one sudo rule naming
  exactly it; the container and VM abilities act only on the names in a root-owned list on the
  host (/etc/kastellan/act/<ability>.allow), every action goes to the host's syslog.

What isn't installed can't be run, whatever the hub asks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import asyncssh

from ..access import PYTHON_CHECK, authorize, port_of
from ..config import VM_NAME, Device
from ..ssh import Keys, Target
from .collector import digest, remove_user

log = logging.getLogger(__name__)

USER = "kastellan-act"
BASE = "/usr/local/lib/kastellan/act"
ABILITIES = {
    "update-container": "Update containers (pull the image, or a newer tag of it, and recreate it"
    " with the same settings)",
    "rollback-container": "Go back to a container's previous image (kept by an update)",
    "restart-container": "Restart containers",
    "start-container": "Start stopped containers",
    "stop-container": "Stop containers",
    "container-logs": "Read containers' logs (the last lines)",
    "unit-logs": "Read services' logs (the last lines of a systemd unit's journal)",
    "journal": "Read the host's journal as it goes on, for the Logs page (logins, services, kernel,"
    " containers that log to journald)",
    "docker-logs": "Read every container's log as it goes on, for the Logs page (through Docker)",
    "vm-start": "Start virtual machines (libvirt)",
    "vm-shutdown": "Shut virtual machines down (as the power button would)",
    "vm-reboot": "Restart virtual machines",
    "vm-force-off": "Force virtual machines off (like pulling the plug: for a guest that hangs)",
    "upgrade-packages": "Install system updates (apt: keeps your config files, removes nothing)",
    "reboot": "Reboot the host (in a minute, so it can still be cancelled there)",
}
CONTAINER_ABILITIES = (
    "update-container",
    "rollback-container",
    "restart-container",
    "start-container",
    "stop-container",
    "container-logs",
)
UNIT_ABILITIES = ("unit-logs",)
READING = ("container-logs", "unit-logs", "journal", "docker-logs")  # change nothing
REPLACING = ("update-container", "rollback-container")  # with act-containers (the swap)
VM_ABILITIES = ("vm-start", "vm-shutdown", "vm-reboot", "vm-force-off")
# The same patterns as act-common's and act-dispatch's (a test checks); always with fullmatch.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")  # an image tag (update-container)
UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9@_.:-]{0,200}\.(service|socket|timer|mount|path|target)")
CURSOR = re.compile(r"-|[A-Za-z0-9=;]{1,400}")  # journald's, or "-" for none
SINCE = re.compile(r"-|\d{1,12}(\.\d{1,9})?")  # Docker's log time "<s>.<ns>", or "-" for none
# Reading where it left off (no list of names): the pattern of where.
CURSORS = {"journal": CURSOR, "docker-logs": SINCE}
CURSOR_ABILITIES = tuple(CURSORS)


def _script(name: str) -> str:
    return (files("kastellan.host") / "scripts" / name).read_text()


def ability_script(ability: str) -> str:
    parts = ["act-common", *(["act-containers"] if ability in REPLACING else []), f"act-{ability}"]
    return "\n\n".join(_script(p).rstrip("\n") for p in parts) + "\n"


def install_script(
    chosen: set[str],
    containers: list[str],
    public_key: str,
    hub_ip: str | None,
    user: str = USER,
    vms: list[str] | None = None,
    allowed: dict[str, list[str]] | None = None,
) -> str:
    """Shell commands, run as root, that give Kastellan exactly the chosen abilities on a host
    (removing any others): for the container abilities on `containers`, for the VM abilities on
    `vms` ("*" for all)."""
    q = shlex.quote
    vms = vms or []
    unknown = chosen - set(ABILITIES)
    if unknown:
        raise ValueError(f"unknown abilities: {', '.join(sorted(unknown))}")
    if any(c != "*" and not NAME.fullmatch(c) for c in containers):
        raise ValueError("container names are letters, digits, _ . - (or * for all)")
    if any(v != "*" and not VM_NAME.fullmatch(v) for v in vms):
        raise ValueError("VM names are letters, digits, _ . + - (or * for all)")
    if allowed is not None:
        if set(allowed) != chosen:
            raise ValueError("allowed must name exactly the chosen abilities")
        for ability, targets in allowed.items():
            pattern = (
                NAME
                if ability in CONTAINER_ABILITIES
                else VM_NAME
                if ability in VM_ABILITIES
                else UNIT
                if ability in UNIT_ABILITIES
                else None
            )
            if len(targets) > 200 or any(
                pattern is None or (t != "*" and not pattern.fullmatch(t)) for t in targets
            ):
                raise ValueError(f"invalid targets for {ability}")
    sudoers = f"/etc/sudoers.d/{user}"
    lines = [
        "set -eu",
        "umask 022",
        *PYTHON_CHECK.splitlines(),
        f"id -u {q(user)} >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh {q(user)}",
        "# The scripts and lists: root's; nobody else may change them.",
        f"install -d -o root -g root -m 755 {BASE} /etc/kastellan /etc/kastellan/act",
    ]

    def put(path: str, text: str, mode: str) -> None:
        end = "KASTELLAN_ACT_EOF"
        lines.extend(
            [
                f"cat > {path}.new <<'{end}'",
                text.rstrip("\n"),
                end,
                f"chown root:root {path}.new && chmod {mode} {path}.new && mv {path}.new {path}",
            ]
        )

    put(f"{BASE}/dispatch", _script("act-dispatch"), "644")
    rules = []
    for ability in sorted(ABILITIES):
        script = f"{BASE}/{ability}"
        if ability in chosen:
            put(script, ability_script(ability), "644")
            # Container and VM abilities take one argument (checked by the script); reboot none.
            # Unit logs: any unit's (reading only; the ability itself is the choice).
            listed = (
                containers
                if ability in CONTAINER_ABILITIES
                else vms
                if ability in VM_ABILITIES
                else ["*"]
                if ability in UNIT_ABILITIES
                else None
            )
            if listed is not None and allowed is not None:
                listed = allowed[ability]
            args = " *" if listed is not None or ability in CURSOR_ABILITIES else ' ""'
            rules.append(f"{user} ALL=(root) NOPASSWD: $py -I -S {script}{args}")
            if listed is not None:
                # The names as quoted words, not in a heredoc: no name can end it early.
                allow = f"/etc/kastellan/act/{ability}.allow"
                names = " ".join(q(c) for c in listed)
                lines.append(f"printf '%s\\n' {names} > {allow}.new" if listed else f": > {allow}.new")
                lines.append(
                    f"chown root:root {allow}.new && chmod 644 {allow}.new && mv {allow}.new {allow}"
                )
        else:
            lines.append(f"rm -f {script} /etc/kastellan/act/{ability}.allow  # not chosen")
    lines += ["# One sudo rule per chosen ability, naming exactly its script."]
    if rules:
        lines.append(f"cat > {sudoers}.new <<KASTELLAN_SUDO_EOF")
        lines += rules
        lines += [
            "KASTELLAN_SUDO_EOF",
            f"chmod 440 {sudoers}.new",
            f"if ! visudo -cf {sudoers}.new >/dev/null; then",
            f"  rm -f {sudoers}.new; echo 'bad sudo rule' >&2; exit 1",
            "fi",
            f"mv {sudoers}.new {sudoers}",
        ]
    else:
        lines.append(f"rm -f {sudoers}")
    lines += [
        "# Kastellan's act key may run the dispatcher, and nothing else (and only from the hub).",
        f"key={q(public_key)}",
        f"hub={q(hub_ip or '')}",
        f'cmd="$py -I -S {BASE}/dispatch"',
        'openssh_line="${hub:+from=\\"$hub\\",}restrict,command=\\"$cmd\\" $key"',
        'limits="no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding"',
        'dropbear_line="command=\\"$cmd\\",$limits $key"',
    ]
    for ln in authorize(user, "OPENSSH_LINE", "DROPBEAR_LINE", only=True):
        lines.append(
            ln.replace("OPENSSH_LINE", '"$openssh_line"').replace("DROPBEAR_LINE", '"$dropbear_line"')
        )
    what = ", ".join(sorted(chosen)) or "nothing (only listing)"
    lines.append(f"echo {q(f'The actor is set up; Kastellan may: {what}.')}")
    return "\n".join(lines) + "\n"


def uninstall_script(user: str = USER) -> str:
    """Shell commands, run as root, that take the actor off a host again (its log stays)."""
    return (
        "\n".join(
            [
                "set -eu",
                f"rm -f /etc/sudoers.d/{user}",
                f"rm -rf {BASE} /etc/kastellan/act",
                *remove_user(user),
                "rmdir /etc/kastellan /usr/local/lib/kastellan 2>/dev/null || true  # if empty",
                "echo 'The actor is removed from this host.'",
            ]
        )
        + "\n"
    )


def target_of(device: Device) -> Target:
    if device.act is None:
        raise ValueError("the device has no act: in config.yaml")
    return Target(device.host, port_of(device, device.act), device.act.user)


async def _run(keys: Keys, device: Device, words: list[str], timeout: float) -> tuple[int, str, str]:
    async with await keys.connect(target_of(device), "act") as conn:
        r = await conn.run(" ".join(words), check=False, stdin=asyncssh.DEVNULL, timeout=timeout)
    return r.exit_status or 0, str(r.stdout or "")[:65536], str(r.stderr or "")[:4096]


async def ask(keys: Keys, device: Device) -> tuple[dict[str, list[str]], list[str]]:
    """What the host lets Kastellan do (ability -> the containers it may act on, [] for none or
    for reboot), and which of its scripts are older than this hub's ("dispatch" or abilities;
    a host set up before scripts reported themselves has all of them older)."""
    code, out, err = await _run(keys, device, ["abilities"], 30)
    if code != 0:
        raise RuntimeError(err.strip().splitlines()[-1] if err.strip() else f"exit {code}")
    return parse_abilities(out)


def parse_abilities(out: str) -> tuple[dict[str, list[str]], list[str]]:
    """The dispatcher's `abilities` answer, as `ask` returns it."""
    lines = out.splitlines()
    names = [a for a in (lines[0].split() if lines else []) if a in ABILITIES]
    result: dict[str, list[str]] = {a: [] for a in names}
    seen: dict[str, str] = {}
    for line in lines[1:]:
        ability, _, rest = line.partition(":")
        if ability in result:
            pattern = VM_NAME if ability in VM_ABILITIES else UNIT if ability in UNIT_ABILITIES else NAME
            result[ability] = [c for c in rest.split() if c == "*" or pattern.fullmatch(c)]
        elif ability == "versions":
            seen = dict(w.split("=", 1) for w in rest.split() if "=" in w)
    expected = {"dispatch": digest(_script("act-dispatch"))} | {a: digest(ability_script(a)) for a in names}
    return result, [n for n, h in expected.items() if seen.get(n) != h]


async def abilities(keys: Keys, device: Device) -> dict[str, list[str]]:
    return (await ask(keys, device))[0]


STEP = "KASTELLAN-STEP "  # an ability's line on how far it got (act-common's step())
# Longer than the script on the host allows itself, so the hub hears how it ended:
# update-container: pull 900 + stop 90 + settling 600 + Docker calls; upgrade-packages:
# dpkg 1200 + update 300 + upgrade 2400; vm-shutdown: ACPI 60 + waiting 180.
LIMITS = {
    "update-container": 2100,
    "rollback-container": 1200,
    "upgrade-packages": 4200,
    "restart-container": 180,
    "start-container": 180,
    "stop-container": 180,
    "container-logs": 60,
    "unit-logs": 60,
    "journal": 90,
    "docker-logs": 120,
    "vm-start": 180,
    "vm-shutdown": 300,
    "vm-reboot": 180,
    "vm-force-off": 120,
    "reboot": 60,
}


async def act(
    keys: Keys,
    device: Device,
    ability: str,
    name: str | None = None,
    on_step: Callable[[str], None] | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Ask the host to do one thing (to the container or VM `name`, for those abilities); its
    answer (JSON) or RuntimeError with its reason. Its steps (how far it got) go to `on_step` as
    they come; scripts older than steps send none."""
    if ability not in ABILITIES:
        raise ValueError("unknown ability")
    if tag is not None and (ability != "update-container" or not TAG.fullmatch(tag)):
        raise ValueError("only update-container takes a tag (letters, digits, _ . -)")
    words = [ability]
    if ability in CONTAINER_ABILITIES:
        if not name or not NAME.fullmatch(name):
            raise ValueError("a container name is letters, digits, _ . -")
        words.append(name)
    elif ability in VM_ABILITIES:
        if not name or not VM_NAME.fullmatch(name):
            raise ValueError("a VM name is letters, digits, _ . + -")
        words.append(name)
    elif ability in CURSOR_ABILITIES:
        if not name or not CURSORS[ability].fullmatch(name):
            raise ValueError("where to read from (or -)")
        words.append(name)
    elif ability in UNIT_ABILITIES:
        if not name or not UNIT.fullmatch(name):
            raise ValueError("a unit name ends in .service, .timer, .socket, ...")
        words.append(name)
    elif name is not None:
        raise ValueError(f"{ability} takes no target")
    if tag is not None:
        words.append(tag)
    errors: list[str] = []
    async with asyncio.timeout(LIMITS[ability]), await keys.connect(target_of(device), "act") as conn:
        process = await conn.create_process(" ".join(words), stdin=asyncssh.DEVNULL)

        async def read_output() -> str:
            # Journals can contain 2000 entries; truncating their JSON to 64 KB made
            # normal batches unreadable. Still put a limit on remote output.
            try:
                await process.stdout.readexactly(8 * 1024 * 1024)
            except asyncio.IncompleteReadError as e:
                return str(e.partial)
            process.close()
            raise RuntimeError("the host's answer is too large")

        out = asyncio.ensure_future(read_output())  # alongside: neither pipe fills up
        async for line in process.stderr:
            line = str(line).rstrip("\n")
            if line.startswith(STEP):
                text = line[len(STEP) :][:300]
                log.debug("%s %s: %s", device.host, ability, text)
                if on_step is not None:
                    on_step(text)
            elif line.strip():
                errors = [*errors, line][-20:]
        stdout = await out
        code = (await process.wait(check=False)).exit_status
    if code != 0:
        if tag is not None and errors and errors[-1].endswith("wrong arguments"):
            # An actor from before tag changes: its dispatcher takes the name only.
            raise RuntimeError("the actor there can't change tags yet: set it up again (Updates)")
        raise RuntimeError(errors[-1][:300] if errors else f"exit {code}")
    try:
        result = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        raise RuntimeError("the host's answer wasn't readable") from e
    return result if isinstance(result, dict) else {"result": result}
