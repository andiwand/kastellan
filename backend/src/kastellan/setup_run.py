"""Running a setup script on a device once, with a login and password typed on the page (never
stored), as root, and streaming its output to the page as it comes (the setup dialog).

Getting root, as the person chooses:
- the login is root: nothing to do;
- sudo, with the login's password (the usual) or another one;
- su, with root's password (a login that may not use sudo; needs a terminal, so it gets one).

The login happens before anything is streamed, so a wrong password is an ordinary HTTP error.
Then the output follows line by line, and the last line is the outcome:
`KASTELLAN-DONE {"ok": true|false, ...}`. Every outcome is audited (never the passwords).
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import shlex
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal

import asyncssh
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, PrivateAttr

from .access import USER_RULE, port_of, valid_user
from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .config import Device
    from .hub import Hub

DONE = "KASTELLAN-DONE"
ALIVE = "KASTELLAN-ALIVE"  # a stream's "still working" line, ignored by the page
ALIVE_EVERY = 20  # seconds
TIMEOUT = 1800  # seconds the whole script may take


class Login(BaseModel):
    login: str = Field(default="", max_length=32)
    password: str = Field(default="", max_length=1024)  # for this one connection, never kept
    # Or a login stored in the People key ring for this device (its id); `remember`: keep the
    # typed one there (for this device only) once it worked.
    stored: str | None = Field(default=None, max_length=128)
    remember: bool = False
    _remembered: str | None = PrivateAttr(default=None)
    port: int = Field(default=22, ge=1, le=65535)
    # How to get root when the login isn't root: sudo (with `root_password` if it is another
    # password than the login's), or su with root's password.
    elevate: Literal["sudo", "su", "none"] = "sudo"  # none: run as the login itself
    root_password: str | None = Field(default=None, max_length=1024)


def device_of(hub: Hub, id: str) -> Device:
    """The device to set up or act on: 404 if there is none, 400 if it has no address of its own."""
    device = hub.config.devices.get(id)
    if device is None:
        raise HTTPException(404, "no such device")
    if not device.host:  # "" would be this machine: the hub's own
        raise HTTPException(400, f"{id} has no address of its own (a UPS read through its host)")
    return device


def stored_logins(hub: Hub, device: str, user: str | None) -> dict[str, Any]:
    """The People ring's logins a setup on this device may use: the ring open, the admin with
    a way of their own into it, and only logins stored for this device."""
    from .auth import password_tag
    from .vault import PEOPLE

    ring_open = hub.vault.exists and hub.vault.is_open(PEOPLE)
    me = hub.config.users.get(user or "")
    mine = me is not None and hub.vault.has_slot(PEOPLE, user or "", password_tag(me.password))
    entries = [
        {"id": e.id, "name": e.name, "user": e.user}
        for e in (hub.vault.entries() if ring_open and mine else [])
        if e.ring == PEOPLE and e.kind == "password" and device in e.devices
    ]
    return {"vault": hub.vault.exists, "ring_open": ring_open, "stored": entries}


def resolve(hub: Hub, device: str | None, body: Login, user: str | None) -> None:
    """A stored login into the body (its user and password), checked like the terminal's: only
    for the devices it was stored for, read for the signed-in admin who asked. Then the login
    must be a user name (400). Once per request, before `connect`."""
    if body.stored:
        mine = stored_logins(hub, device, user)["stored"] if device is not None else []
        if not any(e["id"] == body.stored for e in mine):
            raise HTTPException(403, "no such stored login for this device (or the People ring is locked)")
        entry = hub.vault.entry(body.stored)
        assert entry is not None
        body.login, body.password = entry.user, hub.vault.get(entry.id, by=user)
        body.remember = False
    elif not body.login or not body.password:
        raise HTTPException(400, "a login and its password, or a stored login")
    if not valid_user(body.login):
        raise HTTPException(400, USER_RULE)


def remember(hub: Hub, device: str, body: Login, user: str | None, address: str | None) -> str | None:
    """The typed login kept in the People ring for this device (asked for); a note, or None."""
    from .vault import PEOPLE, Locked

    if not body.remember or body.stored or not hub.vault.exists:
        return None
    base = f"ssh-{device}-{body.login}".lower().replace("_", "-")
    entry_id, n = base, 2
    while hub.vault.has(entry_id):
        entry_id, n = f"{base}-{n}", n + 1
    name = hub.config.devices[device].name if device in hub.config.devices else device
    try:
        hub.vault.put(
            entry_id,
            body.password,
            ring=PEOPLE,
            kind="password",
            name=f"{name} ({body.login})",
            user=body.login,
            devices=[device],
        )
    except Locked as e:
        return f"not kept: {e}"
    except Exception:  # never the password in a message
        return "not kept: the key ring can't be written"
    hub.audit.record("vault.add", user, address, entry=entry_id, ring=PEOPLE, kind="password")
    return f"kept in the People ring as {entry_id}"


async def connect(
    hub: Hub,
    host: str,
    body: Login,
    audit: Callable[..., None],
    device: str | None = None,
    user: str | None = None,
) -> asyncssh.SSHClientConnection:
    """Log in with the body's login (`resolve` first); HTTP errors for the page (and audited). A
    typed login is kept in the People ring when asked, once it worked."""
    if hub.demo:  # its hosts are made up: a real machine at that address must never be touched
        raise HTTPException(409, "demo mode shows a made-up network; nothing is set up there")
    if not host:  # "" would be this machine: the hub's own
        raise HTTPException(400, "this device has no address of its own")
    try:
        conn = await hub.keys.connect(Target(host, body.port, body.login), password=body.password)
        if device is not None and body.remember:
            note = remember(hub, device, body, user, None)
            body._remembered = note
            if note:
                audit(remembered=note)
        return conn
    except (UnknownHost, ChangedHost) as e:
        audit(outcome="host key")
        raise HTTPException(409, f"{e} (trust the host key first)") from e
    except asyncssh.PermissionDenied as e:
        audit(outcome="login refused")
        raise HTTPException(401, f"{body.login} with that password was refused") from e
    except (TimeoutError, asyncssh.TimeoutError) as e:
        audit(outcome="timed out")
        raise HTTPException(504, "the device didn't answer in time") from e
    except (OSError, asyncssh.Error) as e:
        audit(outcome="unreachable")
        raise HTTPException(502, f"can't reach {host} on port {body.port}: {reason(e)}") from e


# Linux takes at most 128 KB in one argument (MAX_ARG_STRLEN): a script given to `sh -c`
# (sudo, su: their stdin is for the password) larger than this goes compressed.
ARGUMENT_MAX = 100_000


def as_argument(script: str) -> str:
    """The script as `sh -c` takes it: itself, or, when large, packed (gzip, base64) with the
    line that unpacks and runs it (its stdin then is that pipe, never the password)."""
    if len(script.encode()) <= ARGUMENT_MAX:
        return script
    packed = base64.b64encode(gzip.compress(script.encode(), 9)).decode()
    return f"printf '%s' '{packed}' | base64 -d | gzip -dc | sh"


async def _lines(conn: asyncssh.SSHClientConnection, script: str, body: Login) -> AsyncIterator[str | int]:
    """The script's output, line by line, then its exit status."""
    q = shlex.quote
    argument = as_argument(script)
    if body.login == "root" or body.elevate == "none":
        process = await conn.create_process("sh -s", stderr=asyncssh.STDOUT)
        process.stdin.write(script)
        process.stdin.write_eof()
        seen = ""
    elif body.elevate == "sudo":
        seen = ""
        probe = await conn.run("sudo -n true", check=False, stdin=asyncssh.DEVNULL, timeout=20)
        if probe.exit_status == 0:
            # sudo without a password here: none is given (it would end up on the script's stdin).
            process = await conn.create_process(
                f"sudo -n sh -c {q(argument)}", stdin=asyncssh.DEVNULL, stderr=asyncssh.STDOUT
            )
        else:
            # sudo reads the password from stdin (-S), with no prompt; the script is its argument
            # and starts with its stdin closed (exec </dev/null), so it never reads the password.
            process = await conn.create_process(
                f"sudo -S -p '' sh -c {q('exec </dev/null; ' + argument)}", stderr=asyncssh.STDOUT
            )
            process.stdin.write((body.root_password or body.password) + "\n")
            process.stdin.write_eof()
    else:
        # su asks on a terminal: one is requested; the password goes in at its prompt. A marker
        # first tells "root now" apart from a prompt (some systems' su asks for nothing).
        inner = f"echo {MARK}; exec sh -c {q('exec </dev/null; ' + argument)}"
        process = await conn.create_process(
            f"LC_ALL=C su root -c {q(inner)}", term_type="dumb", stderr=asyncssh.STDOUT
        )
        seen = ""
        async with asyncio.timeout(30):
            while MARK not in seen:
                chunk = await process.stdout.read(256)
                if not chunk:
                    break
                seen += chunk
                if "assword" in seen and MARK not in seen:
                    process.stdin.write((body.root_password or "") + "\n")
                    seen = ""
    leftover = seen.split(MARK, 1)[1] if MARK in seen else seen if body.elevate == "su" else ""
    # A password must never reach the page or the audit log, even if something echoes it (a
    # terminal does, unless the program asking turns that off): masked, and a line that held
    # nothing else is dropped.
    secrets = [p for p in (body.password, body.root_password) if p]
    pending = leftover.splitlines(keepends=True) if leftover else []
    while True:
        line = pending.pop(0) if pending else await process.stdout.readline()
        if not line:
            break
        text = "".join(c for c in line.rstrip("\r\n") if c >= " " or c == "\t")
        for secret in secrets:
            text = text.replace(secret, "***")
        if MARK in text or not text.replace("*", "").strip():
            continue
        yield text[:2000]
    result = await process.wait(check=False)
    yield result.exit_status if result.exit_status is not None else 1


MARK = "KASTELLAN-ROOT-NOW"
_running: set[asyncio.Task[Any]] = set()  # setups under way: they finish even if the page goes


def spawn(run: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """`run` as a task of the hub's, not of the request: it finishes when the page goes."""
    task = asyncio.get_running_loop().create_task(run)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


def hub_task(run: Coroutine[Any, Any, None], queue: asyncio.Queue[bytes | None]) -> StreamingResponse:
    """Runs `run` as the hub's task and streams what it puts in `queue` until None."""
    spawn(run)

    async def follow() -> AsyncIterator[bytes]:
        while True:
            # A line now and then while the host is quiet (an image pulled, apt downloading): a
            # proxy in front ends a response that says nothing for a minute, and the page would
            # take the run for failed while it goes on.
            try:
                chunk = await asyncio.wait_for(queue.get(), ALIVE_EVERY)
            except TimeoutError:
                yield f"{ALIVE}\n".encode()
                continue
            if chunk is None:
                return
            yield chunk

    return StreamingResponse(
        follow(), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"}
    )


def stream(
    conn: asyncssh.SSHClientConnection,
    script: str,
    body: Login,
    audit: Callable[..., None],
    after: Callable[[], dict[str, Any]] | None = None,
    keep: list[str] | None = None,
) -> StreamingResponse:
    """Run the script over the open connection and stream its output; on success, `after()`
    (e.g. adding collect: to config.yaml) and its result go into the last line. With `keep`,
    every output line is also kept there (for `after()`), and `KASTELLAN-` data lines aren't
    shown.

    The run belongs to the hub, not to the page: it goes on (and is audited, and `after()`
    runs) when the browser leaves halfway. The page only follows it."""
    audit(outcome="started")
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    if body._remembered:
        queue.put_nowait((body._remembered + "\n").encode())

    async def run() -> None:
        tail: list[str] = []
        outcome: dict[str, Any] = {"ok": False}
        try:
            async with asyncio.timeout(TIMEOUT), conn:
                async for item in _lines(conn, script, body):
                    if isinstance(item, int):
                        outcome = {"ok": item == 0, "exit": item}
                    else:
                        if keep is not None:
                            keep.append(item)
                            if item.startswith("KASTELLAN-CERT "):
                                continue
                        tail = [*tail, item][-5:]
                        queue.put_nowait((item + "\n").encode())
            if not outcome["ok"]:
                wrong = any(
                    "incorrect password" in t or "Authentication failure" in t or "Sorry" in t for t in tail
                )
                outcome["error"] = (
                    "the sudo or root password was refused" if wrong else (tail[-1] if tail else "failed")
                )
        except BaseException as e:  # also when the hub stops: the audit still says so
            outcome = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            if not isinstance(e, Exception):
                audit(outcome=f"interrupted: {type(e).__name__}")
                queue.put_nowait(None)
                raise
        if outcome["ok"] and after is not None:
            try:
                outcome |= after()
            except Exception as e:  # installed all the same; only the config entry is missing
                outcome["config_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if outcome["ok"]:
            audit(
                outcome="done"
                + (f", config not saved: {outcome['config_error']}" if "config_error" in outcome else "")
            )
        else:
            audit(outcome=f"failed: {(outcome.get('error') or '')[-200:]}")
        queue.put_nowait(f"{DONE} {json.dumps(outcome)}\n".encode())
        queue.put_nowait(None)

    return hub_task(run(), queue)


def remove_device_key(hub: Hub, user: str | None, address: str | None, id: str, key: str) -> bool:
    """After removing something from a host: its `<key>:` off the device in config.yaml (checked,
    saved and audited like any edit). Whether it was there."""
    from . import config_edit

    if getattr(hub.config.devices[id], key) is None:
        return False
    old = config_edit.read(hub.settings.config)
    new = config_edit.set_device_key(old, id, key, None)
    hub.check(new)
    hub.save_config(old, new)
    hub.audit.record("config.device.change", user, address, device=id, changes=config_edit.diff(old, new))
    return True


def add_device_key(
    hub: Hub,
    user: str | None,
    address: str | None,
    id: str,
    key: str,
    port: int,
    value: dict | None = None,
    replace: bool = False,
) -> bool:
    """After an install: `<key>: {...}` (collect:, act:, ssh:) on the device in config.yaml, if
    it isn't there yet (or, with `replace`, different); checked and saved like any edit, and
    audited. Whether it was written."""
    from . import config_edit

    device = hub.config.devices[id]
    current = getattr(device, key)
    if current is not None and not replace:
        return False
    old = config_edit.read(hub.settings.config)
    if value is None:
        value = {} if port == port_of(device) else {"port": port}
    if current is not None and current.model_dump(exclude_defaults=True) == value:
        return False  # already so
    new = config_edit.set_device_key(old, id, key, value)
    hub.check(new)
    hub.save_config(old, new)
    hub.audit.record("config.device.change", user, address, device=id, changes=config_edit.diff(old, new))
    return True


def reason(e: BaseException) -> str:
    """What went wrong, in the system's words ("Connect call failed ('10.0.0.2', 22)",
    "No route to host"), not only the kind of error."""
    why = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
    return (why or type(e).__name__)[:200]


def stream_work(
    work: Callable[[Callable[[str], None]], Any],
    audit: Callable[..., None],
) -> StreamingResponse:
    """Any long job of the hub's, streamed like a setup: `work(say)` reports with `say(line)`
    and returns its outcome (a dict); it runs to its end even if the page goes, and is audited
    (started, then the outcome)."""
    audit(outcome="started")
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def say(line: str) -> None:
        queue.put_nowait((line[:2000] + "\n").encode())

    async def run() -> None:
        try:
            result = await work(say)
            outcome = {"ok": True, **(result or {})}
            audit(outcome="done")
        except BaseException as e:
            outcome = {"ok": False, "error": f"{str(e)[:300] or type(e).__name__}"}
            audit(outcome=f"failed: {outcome['error'][:200]}" if isinstance(e, Exception) else "interrupted")
            if not isinstance(e, Exception):
                queue.put_nowait(None)
                raise
        queue.put_nowait(f"{DONE} {json.dumps(outcome)}\n".encode())
        queue.put_nowait(None)

    return hub_task(run(), queue)
