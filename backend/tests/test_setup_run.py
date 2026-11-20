"""The setup runner: getting root by sudo (the login's or another password) or su (root's, at
its prompt), streaming the output, and the outcome line."""

import asyncio
import json

import asyncssh
import pytest

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
