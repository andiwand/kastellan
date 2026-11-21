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
import json
import shlex
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal

import asyncssh
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .ssh import ChangedHost, Target, UnknownHost

if TYPE_CHECKING:
    from .hub import Hub

DONE = "KASTELLAN-DONE"
TIMEOUT = 1800  # seconds the whole script may take


class Login(BaseModel):
    login: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=1024)  # for this one connection, never kept
    port: int = Field(default=22, ge=1, le=65535)
    # How to get root when the login isn't root: sudo (with `root_password` if it is another
    # password than the login's), or su with root's password.
    elevate: Literal["sudo", "su", "none"] = "sudo"  # none: run as the login itself
    root_password: str | None = Field(default=None, max_length=1024)


async def connect(
    hub: Hub, host: str, body: Login, audit: Callable[..., None]
) -> asyncssh.SSHClientConnection:
    """Log in with the typed password; HTTP errors for the page (and audited)."""
    try:
        return await hub.keys.connect(Target(host, body.port, body.login), password=body.password)
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


async def _lines(conn: asyncssh.SSHClientConnection, script: str, body: Login) -> AsyncIterator[str | int]:
    """The script's output, line by line, then its exit status."""
    q = shlex.quote
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
                f"sudo -n sh -c {q(script)}", stdin=asyncssh.DEVNULL, stderr=asyncssh.STDOUT
            )
        else:
            # sudo reads the password from stdin (-S), with no prompt; the script is its argument
            # and starts with its stdin closed (exec </dev/null), so it never reads the password.
            process = await conn.create_process(
                f"sudo -S -p '' sh -c {q('exec </dev/null; ' + script)}", stderr=asyncssh.STDOUT
            )
            process.stdin.write((body.root_password or body.password) + "\n")
            process.stdin.write_eof()
    else:
        # su asks on a terminal: one is requested; the password goes in at its prompt. A marker
        # first tells "root now" apart from a prompt (some systems' su asks for nothing).
        inner = f"echo {MARK}; exec sh -c {q('exec </dev/null; ' + script)}"
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
_running: set[asyncio.Task[None]] = set()  # setups under way: they finish even if the page goes


def _hub_task(run: Coroutine[Any, Any, None], queue: asyncio.Queue[bytes | None]) -> StreamingResponse:
    """Runs `run` as the hub's task and streams what it puts in `queue` until None."""
    task = asyncio.get_running_loop().create_task(run)
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def follow() -> AsyncIterator[bytes]:
        while (chunk := await queue.get()) is not None:
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

    return _hub_task(run(), queue)


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
        value = {} if port == (device.ssh.port if device.ssh else 22) else {"port": port}
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

    return _hub_task(run(), queue)
