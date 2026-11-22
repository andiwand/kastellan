"""The setup runner: getting root by sudo (the login's or another password) or su (root's, at
its prompt), streaming the output, and the outcome line."""

import asyncio
import json

import asyncssh
import pytest

from kastellan import setup_run
from kastellan.setup_run import Login, _lines, stream
from kastellan.ssh import Keys


@pytest.fixture
async def server(tmp_path):
    seen: list[tuple[str, str]] = []

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

        def password_auth_supported(self) -> bool:
            return True

        def validate_password(self, username: str, password: str) -> bool:
            return password == "mine"

    nopasswd: list[bool] = []

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        if command == "sudo -n true":
            process.exit(0 if nopasswd else 1)
            return
        if command.startswith("LC_ALL=C su root"):
            process.stdout.write("Password:")
            answer = (await process.stdin.readline()).strip()
            seen.append(("su", answer))
            if answer != "rootpw":
                process.stdout.write("\r\nsu: Authentication failure\r\n")
                process.exit(1)
                return
            process.stdout.write("\r\nstep 1\r\nstep 2\r\n")
            process.exit(0)
            return
        data = await process.stdin.read()
        seen.append((command.split()[0], data.splitlines()[0] if data else ""))
        process.stdout.write("installed\n")
        process.exit(0)

    host_key = asyncssh.generate_private_key("ssh-ed25519")
    srv = await asyncssh.create_server(
        Server, "127.0.0.1", 0, server_host_keys=[host_key], process_factory=handle
    )
    port = srv.sockets[0].getsockname()[1]
    keys = Keys(tmp_path / "ssh")
    keys.folder.mkdir(parents=True)
    keys.known_hosts.write_text(f"[127.0.0.1]:{port} {host_key.export_public_key().decode().strip()}\n")
    yield keys, port, seen, nopasswd
    srv.close()


async def run(keys: Keys, port: int, **body) -> list[str | int]:
    login = Login(login="andreas", password="mine", port=port, **body)
    from kastellan.ssh import Target

    async with await keys.connect(Target("127.0.0.1", port, "andreas"), password="mine") as conn:
        return [item async for item in _lines(conn, "echo hi\n", login)]


async def test_sudo_with_the_logins_or_another_password(server) -> None:
    keys, port, seen, _ = server
    assert await run(keys, port) == ["installed", 0]
    assert seen[-1] == ("sudo", "mine")  # the login's own password, on sudo's stdin
    await run(keys, port, root_password="other")
    assert seen[-1] == ("sudo", "other")


async def test_su_with_roots_password(server) -> None:
    keys, port, seen, _ = server
    assert await run(keys, port, elevate="su", root_password="rootpw") == ["step 1", "step 2", 0]
    assert seen[-1] == ("su", "rootpw")
    out = await run(keys, port, elevate="su", root_password="wrong")
    assert out[-1] == 1 and "su: Authentication failure" in out


async def test_the_outcome_line(server) -> None:
    keys, port, _, _ = server
    from kastellan.ssh import Target

    audited: list[dict] = []
    conn = await keys.connect(Target("127.0.0.1", port, "andreas"), password="mine")
    response = stream(
        conn,
        "x",
        Login(login="andreas", password="mine", port=port, elevate="su", root_password="nope"),
        lambda **kw: audited.append(kw),
    )
    chunks = [c.decode() async for c in response.body_iterator]
    last = chunks[-1]
    assert last.startswith("KASTELLAN-DONE ")
    outcome = json.loads(last.removeprefix("KASTELLAN-DONE "))
    assert not outcome["ok"] and "refused" in outcome["error"]
    assert audited[-1]["outcome"].startswith("failed") and "nope" not in json.dumps(audited)
    await asyncio.sleep(0)


async def test_passwordless_sudo_gets_no_password(server) -> None:
    keys, port, seen, nopasswd = server
    nopasswd.append(True)
    assert await run(keys, port) == ["installed", 0]
    assert seen[-1] == ("sudo", "")  # nothing on its stdin: no password where the script could read it


async def test_setup_reports_when_the_login_could_not_be_kept(server) -> None:
    from kastellan.ssh import Target

    keys, port, _, _ = server
    conn = await keys.connect(Target("127.0.0.1", port, "andreas"), password="mine")
    body = Login(login="andreas", password="mine", port=port)
    body._remembered = "not kept: the People ring is locked"
    response = stream(conn, "echo hi", body, lambda **kw: None)
    chunks = [chunk.decode() async for chunk in response.body_iterator]
    assert chunks[0] == body._remembered + "\n"


async def test_a_page_that_leaves_doesnt_stop_it(server) -> None:
    """The run is the hub's: the browser leaving halfway still gets it audited and finished."""
    keys, port, _, _ = server
    from kastellan.ssh import Target

    audited: list[dict] = []
    after_ran: list[bool] = []
    conn = await keys.connect(Target("127.0.0.1", port, "andreas"), password="mine")
    response = stream(
        conn,
        "x",
        Login(login="andreas", password="mine", port=port),
        lambda **kw: audited.append(kw),
        lambda: after_ran.append(True) or {},
    )
    reader = response.body_iterator.__aiter__()
    await reader.__anext__()  # the first line, then the page goes away
    await reader.aclose()
    for _ in range(100):
        if after_ran:
            break
        await asyncio.sleep(0.02)
    assert after_ran == [True]
    assert [a["outcome"] for a in audited] == ["started", "done"]


async def test_a_quiet_run_says_it_is_alive(monkeypatch) -> None:
    """A proxy ends a response that says nothing for a minute: while the host is quiet, the
    stream sends a line the page ignores."""
    monkeypatch.setattr(setup_run, "ALIVE_EVERY", 0.05)
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    async def run() -> None:
        await asyncio.sleep(0.2)
        queue.put_nowait(b"KASTELLAN-DONE {}\n")
        queue.put_nowait(None)

    response = setup_run.hub_task(run(), queue)
    lines = [chunk async for chunk in response.body_iterator]
    assert lines[-1] == b"KASTELLAN-DONE {}\n"
    assert lines.count(b"KASTELLAN-ALIVE\n") >= 2


def test_a_large_script_goes_packed_as_an_argument() -> None:
    import subprocess

    from kastellan.host import actor
    from kastellan.setup_run import ARGUMENT_MAX, as_argument

    small = "echo hi"
    assert as_argument(small) == small
    script = actor.install_script(set(actor.ABILITIES), ["*"], "ssh-ed25519 AAAA x", "10.0.0.1", vms=["*"])
    script = f"cat <<'KASTELLAN_TEST'\n{script}\nKASTELLAN_TEST\n"
    packed = as_argument(script)
    assert (
        len(script.encode()) > 128 * 1024 > ARGUMENT_MAX > len(packed.encode())
    )  # Linux's limit per argument
    out = subprocess.run(["sh", "-c", "exec </dev/null; " + packed], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.rstrip("\n") == script.split("\n", 1)[1].rsplit(
        "\nKASTELLAN_TEST", 1
    )[0].rstrip("\n")


def test_new_abilities_until_set_up_or_not_needed(tmp_path) -> None:
    from kastellan.abilities import Offered

    offered = Offered(tmp_path / "offered.json")
    assert offered.new("sv1", "actor", None) == []  # not known yet
    assert offered.new("sv1", "actor", ["update-container", "journal"]) == [
        "rollback-container",
        "container-logs",
        "unit-logs",
        "docker-logs",
    ]
    offered.passed("sv1", "actor", {"rollback-container", "container-logs", "unit-logs"})
    assert offered.new("sv1", "actor", ["journal"]) == ["docker-logs"]
    offered.passed("sv1", "actor", ["docker-logs"])
    assert offered.new("sv1", "actor", []) == ["journal"]  # removed again: offered again
    assert offered.new("ap", "openwrt", ["logs"]) == ["backup", "scan", "bandwidth", "roaming"]


def test_a_stored_login_only_for_its_device(tmp_path) -> None:
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from kastellan import setup_run
    from kastellan.auth import hash_password, password_tag
    from kastellan.config import Config
    from kastellan.vault import PEOPLE, Vault

    hashed = hash_password("ada's password")
    vault = Vault(tmp_path / "vault.json")
    vault.create("ada", "ada's password", password_tag(hashed))
    vault.put(
        "ssh-sv1-root", "root pw", ring=PEOPLE, kind="password", name="sv1", user="root", devices=["sv1"]
    )
    config = Config.model_validate(
        {
            "devices": {"sv1": {"host": "sv1"}, "nas": {"host": "nas"}},
            "users": {"ada": {"role": "admin", "password": hashed}},
        }
    )
    hub = SimpleNamespace(vault=vault, config=config)
    assert [e["id"] for e in setup_run.stored_logins(hub, "sv1", "ada")["stored"]] == ["ssh-sv1-root"]
    assert setup_run.stored_logins(hub, "nas", "ada")["stored"] == []
    body = setup_run.Login(stored="ssh-sv1-root")
    setup_run.resolve(hub, "sv1", body, "ada")
    assert (body.login, body.password) == ("root", "root pw")
    with pytest.raises(HTTPException):  # never sent to another device
        setup_run.resolve(hub, "nas", setup_run.Login(stored="ssh-sv1-root"), "ada")
    with pytest.raises(HTTPException):  # a typed login needs both
        setup_run.resolve(hub, "sv1", setup_run.Login(login="root"), "ada")
    with pytest.raises(HTTPException, match="user names"):  # a whole name, no newline after it
        setup_run.resolve(hub, "sv1", setup_run.Login(login="root\n", password="pw"), "ada")


def test_concurrent_ability_dismissals_keep_every_device(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from kastellan.abilities import Offered

    offered = Offered(tmp_path / "offered.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: offered.passed(f"host{n}", "actor", ["journal"]), range(40)))
    assert all("journal" not in offered.new(f"host{n}", "actor", []) for n in range(40))
