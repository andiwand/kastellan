"""The actor's scripts, run for real against a fake Docker API (a Unix socket), and its install."""

import http.server
import json
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from kastellan.host import actor, collector

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBgfPq3YGT1qT0EzQAp9VUdHPu+P2Q0Ck0tUjJmQNlpc kastellan-act"


class FakeDocker:
    """Just enough of the Docker API for the abilities: containers by name or id, images by
    reference, and a log of what was asked."""

    def __init__(self, new_image_runs: bool = True, newer: bool = True) -> None:
        self.calls: list[str] = []
        self.api, self.min_api = "1.52", "1.44"  # as Docker 29 answers /version
        self.images = {"me/app:1": "sha256:" + ("b" if newer else "a") * 64}
        self.image_info = {
            "sha256:" + "a" * 64: {
                "RepoDigests": ["me/app@sha256:" + "1" * 64],
                "Config": {
                    "Env": ["PATH=/old", "APP_VERSION=1.0"],
                    "Labels": {"version": "1.0"},
                    "Cmd": ["run"],
                },
            },
            "sha256:" + "b" * 64: {"RepoDigests": ["me/app@sha256:" + "2" * 64], "Config": {}},
        }
        self.crash_loop = False
        self.containers = {
            "c" * 64: {
                "Id": "c" * 64,
                "Name": "/app",
                "Image": "sha256:" + "a" * 64,
                "Config": {
                    "Image": "me/app:1",
                    "Hostname": "c" * 12,
                    "Env": ["SECRET=x", "PATH=/old", "APP_VERSION=1.0"],
                    "Labels": {"version": "1.0", "mine": "yes"},
                    "Cmd": ["run"],
                },
                "HostConfig": {
                    "NetworkMode": "bridge",
                    "Binds": ["/data:/data"],
                    "RestartPolicy": {"Name": "always"},
                },
                "NetworkSettings": {"Networks": {"bridge": {"Aliases": None}}},
                "Mounts": [{"Type": "volume", "Name": "anon1", "Destination": "/cache", "RW": True}],
                "State": {"Running": True, "Status": "running"},
            }
        }
        self.new_image_runs = new_image_runs
        self.created: dict | None = None
        self.created_id = ""  # the container made last (the "new" one)

    def find(self, key: str) -> dict | None:
        """As Docker: the full id, then the exact name, then a unique id prefix."""
        for c in self.containers.values():
            if c["Id"] == key:
                return c
        for c in self.containers.values():
            if c["Name"].lstrip("/") == key:
                return c
        matches = [c for c in self.containers.values() if c["Id"].startswith(key)]
        return matches[0] if len(matches) == 1 else None

    def handle(self, method: str, path: str, body: bytes) -> tuple[int, object]:
        from urllib.parse import parse_qs, unquote, urlsplit

        url = urlsplit(path)
        p, q = unquote(url.path), {k: v[0] for k, v in parse_qs(url.query).items()}
        if p == "/version":
            return 200, {"ApiVersion": self.api, "MinAPIVersion": self.min_api}
        m = re.match(r"^/v(\d+)\.(\d+)(/.*)$", p)
        asked = (int(m.group(1)), int(m.group(2))) if m else None
        if asked is None or asked < tuple(int(x) for x in self.min_api.split(".")):
            return 400, {"message": f"client version {asked} is too old"}  # as Docker 29 refuses
        p, self.spoke = m.group(3), asked
        self.calls.append(f"{method} {p}")
        if method == "POST" and p == "/images/create":
            return 200, None
        if method == "GET" and p.startswith("/images/sha256:") and p.endswith("/json"):
            ref = p[len("/images/") : -len("/json")]
            return (
                (200, {"Id": ref, **self.image_info[ref]})
                if ref in self.image_info
                else (404, {"message": "no"})
            )
        if method == "GET" and p.startswith("/images/") and p.endswith("/json"):
            ref = p[len("/images/") : -len("/json")]
            if ref not in self.images:
                return 404, {"message": "no such image"}
            return 200, {"Id": self.images[ref], **self.image_info.get(self.images[ref], {})}
        if method == "POST" and p.startswith("/images/") and p.endswith("/tag"):
            self.images[f"{q['repo']}:{q['tag']}"] = p[len("/images/") : -len("/tag")]
            return 201, None
        if method == "POST" and p == "/containers/create":
            spec = json.loads(body)
            self.created = spec
            cid = next(x * 64 for x in "d0123456789" if x * 64 not in self.containers)
            self.created_id = cid
            self.containers[cid] = {
                "Id": cid,
                "Name": "/" + q["name"],
                "Image": self.images[spec["Image"]],
                "Config": spec,
                "HostConfig": spec["HostConfig"],
                "NetworkSettings": {"Networks": {}},
                "Mounts": [],
                "State": {"Running": False, "Status": "created"},
            }
            return 201, {"Id": cid}
        if method == "GET" and p.endswith("/logs"):
            frame = lambda stream, text: bytes([stream, 0, 0, 0]) + len(text).to_bytes(4, "big") + text  # noqa: E731
            return 200, frame(1, b"2026-11-21T10:00:00Z hello\n") + frame(
                2, b"2026-11-21T10:00:01Z \x1b[31merror\x1b[0m: oops\n"
            )
        if method == "GET" and p == "/containers/json":
            return 200, [{"Id": c["Id"]} for c in self.containers.values()]
        if method == "POST" and p.startswith("/networks/") and p.endswith("/connect"):
            return 200, None
        parts = p.split("/")
        if len(parts) >= 3 and parts[1] == "containers":
            c = self.find(parts[2])
            if c is None:
                return 404, {"message": "no such container"}
            action = parts[3] if len(parts) > 3 else ""
            if method == "GET" and action == "json" and c["Id"] == self.created_id and self.crash_loop:
                c["RestartCount"] = c.get("RestartCount", 0) + 1  # it keeps restarting
                return 200, c
            if method == "GET" and action == "json":
                return 200, c
            if action == "stop":
                c["State"] = {"Running": False, "Status": "exited", "ExitCode": 0}
                return 204, None
            if action == "rename":
                c["Name"] = "/" + q["name"]
                return 204, None
            if action in ("start", "restart"):
                works = c["Id"] != self.created_id or self.new_image_runs
                c["State"] = {
                    "Running": works,
                    "Status": "running" if works else "exited",
                    "ExitCode": 0 if works else 1,
                }
                return 204, None
            if method == "DELETE":
                del self.containers[c["Id"]]
                return 204, None
        return 404, {"message": f"fake: {method} {p}"}


@pytest.fixture
def host():
    folder = Path(tempfile.mkdtemp(prefix="kact", dir="/tmp"))  # short: a socket path's limit
    sock = folder / "d.sock"
    (folder / "allow").mkdir()
    fake = FakeDocker()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _do(self):
            n = int(self.headers.get("content-length") or 0)
            status, data = fake.handle(self.command, self.path, self.rfile.read(n) if n else b"")
            raw = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_DELETE = _do

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            conn, _ = super().get_request()
            return conn, ("local", 0)

    server = Server(str(sock), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def run(ability: str, *args: str, allow: str = "app\n") -> subprocess.CompletedProcess:
        (folder / "allow" / f"{ability}.allow").write_text(allow)
        code = actor.ability_script(ability)
        code = code.replace('SOCKET = "/var/run/docker.sock"', f"SOCKET = {str(sock)!r}")
        code = code.replace("/etc/kastellan/act/", f"{folder}/allow/").replace(
            "time.sleep(2)", "time.sleep(0.01)"
        )
        code = code.replace('LOCK = "/run/kastellan-act.lock"', f"LOCK = {str(folder / 'lock')!r}")
        code = code.replace(">= 15:", ">= 0.05:")  # steady for a moment is enough in tests
        script = folder / ability
        script.write_text(code)
        return subprocess.run(
            [sys.executable, "-I", str(script), *args], capture_output=True, text=True, timeout=60
        )

    run.folder = folder  # type: ignore[attr-defined]
    yield fake, run
    server.shutdown()
    sock.unlink(missing_ok=True)


def steps(r: subprocess.CompletedProcess) -> list[str]:
    return [
        ln.removeprefix("KASTELLAN-STEP ") for ln in r.stderr.splitlines() if ln.startswith("KASTELLAN-STEP ")
    ]


def test_update_recreates_with_the_same_settings(host) -> None:
    fake, run = host
    r = run("update-container", "app")
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert isinstance(result.pop("seconds"), int | float)
    assert result == {
        "container": "app",
        "image": "me/app:1",
        "updated": True,
        "from": "a" * 12,
        "to": "b" * 12,
    }
    # How far it got, as it went (for the page): in order, on stderr.
    assert steps(r) == [
        "checking app (me/app:1)",
        "pulling me/app:1",
        "comparing the pulled image with the running one",
        "creating the new container",
        "stopping the old container",
        "starting the new container",
        "waiting until it runs steadily (up to 30 s)",
        "removing the old container",
        "keeping the previous image for going back",
    ]
    assert fake.images["kastellan-previous:app"] == "sha256:" + "a" * 64
    spec = fake.created
    assert spec["Image"] == "me/app:1" and "Hostname" not in spec  # Docker's default hostname goes
    # The old image's defaults go (the new image brings its own); the container's own stay.
    assert spec["Env"] == ["SECRET=x"] and spec["Labels"] == {"mine": "yes"} and "Cmd" not in spec
    assert spec["HostConfig"]["RestartPolicy"] == {"Name": "always"}
    assert spec["HostConfig"]["Binds"] == ["/data:/data", "anon1:/cache"]  # the anonymous volume stays
    assert list(fake.containers) == ["d" * 64] and fake.find("app")["State"]["Running"]  # the old one gone
    assert "SECRET" not in r.stdout  # nothing of its settings comes back


def test_a_failing_new_container_brings_the_old_one_back(host) -> None:
    fake, run = host
    fake.new_image_runs = False
    r = run("update-container", "app")
    assert r.returncode == 1 and r.stderr.strip().splitlines()[-1].endswith("the old container runs again")
    assert steps(r)[-1].endswith("putting the old container back")
    old = fake.find("app")
    assert old["Id"] == "c" * 64 and old["State"]["Running"] and "d" * 64 not in fake.containers


def test_going_back_to_the_previous_image_and_forth(host) -> None:
    fake, run = host
    a, b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    assert run("rollback-container", "app").stdout == ""  # nothing kept yet
    assert run("update-container", "app").returncode == 0
    r = run("rollback-container", "app")
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["rolled_back"] and (result["from"], result["to"]) == ("b" * 12, "a" * 12)
    assert fake.find("app")["Image"] == a and fake.images["me/app:1"] == a  # its name, here
    assert fake.images["kastellan-previous:app"] == b  # undo: back again
    assert fake.find("app")["Config"]["Image"] == "me/app:1"  # still updates by its name
    r = run("rollback-container", "app")
    assert r.returncode == 0 and fake.find("app")["Image"] == b

    # A kept image of another repository is never used; one already running isn't news.
    fake.image_info[a]["RepoDigests"] = ["evil/app@sha256:" + "1" * 64]
    r = run("rollback-container", "app")
    assert r.returncode == 1 and "isn't me/app:1's" in r.stderr
    fake.images["kastellan-previous:app"] = b
    assert "already runs" in run("rollback-container", "app").stderr


def test_a_failing_rollback_points_the_name_back(host) -> None:
    fake, run = host
    assert run("update-container", "app").returncode == 0
    fake.new_image_runs = False
    current = fake.find("app")
    fake.containers = {"e" * 64: {**current, "Id": "e" * 64}}  # the updated one, as if from before
    r = run("rollback-container", "app")
    assert r.returncode == 1 and "the old container runs again" in r.stderr
    assert fake.images["me/app:1"] == "sha256:" + "b" * 64  # the name points where it did


def test_update_to_another_tag_and_back(host) -> None:
    fake, run = host
    a, b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    fake.images = {"me/app:1": a, "me/app:2": b}
    r = run("update-container", "app", "2")
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert (result["image"], result["from_tag"], result["to_tag"]) == ("me/app:2", "1", "2")
    assert "pulling me/app:2" in steps(r)
    c = fake.find("app")
    assert c["Config"]["Image"] == "me/app:2" and c["Image"] == b
    assert c["Config"]["Labels"] == {"mine": "yes", "kastellan.previous-image": "me/app:1"}
    assert fake.images["kastellan-previous:app"] == a

    # Back: to the tag it ran, not the old image under the new tag's name.
    r = run("rollback-container", "app")
    assert r.returncode == 0, r.stderr
    c = fake.find("app")
    assert (c["Config"]["Image"], c["Image"]) == ("me/app:1", a) and fake.images["me/app:2"] == b
    assert c["Config"]["Labels"]["kastellan.previous-image"] == "me/app:2"  # and forth again
    assert json.loads(r.stdout)["image"] == "me/app:1"

    # A same-tag update forgets the other tag (the kept image is now this tag's).
    fake.images["me/app:1"] = b
    assert run("update-container", "app").returncode == 0
    assert "kastellan.previous-image" not in fake.find("app")["Config"]["Labels"]


def test_a_tag_only_of_the_same_image(host) -> None:
    fake, run = host
    a, b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    fake.images = {"me/app:1": a, "me/app:2": b}
    fake.image_info[b]["RepoDigests"] = ["evil/app@sha256:" + "3" * 64]  # someone else's under it
    r = run("update-container", "app", "2")
    assert r.returncode == 1 and "isn't me/app:1's image" in r.stderr
    assert fake.find("app")["Image"] == a
    for bad in ("../x", "-x", "a b"):
        assert run("update-container", "app", bad).returncode == 2
    assert run("update-container", "app", "1", "2").returncode == 2


def test_nothing_newer_changes_nothing(host) -> None:
    fake, run = host
    fake.images["me/app:1"] = "sha256:" + "a" * 64
    r = run("update-container", "app")
    assert r.returncode == 0 and json.loads(r.stdout)["updated"] is False
    assert not any(c.startswith("POST /containers/create") for c in fake.calls)


def test_only_what_the_host_allows(host) -> None:
    fake, run = host
    assert run("update-container", "app", allow="other\n").returncode == 3
    assert run("update-container", "app", allow="*\n").returncode == 0
    assert run("update-container", "../app").returncode == 2
    assert run("update-container", "app", "more", "extra").returncode == 2
    assert run("restart-container", "app").returncode == 0
    app_id = fake.find("app")["Id"]
    assert f"POST /containers/{app_id}/restart" in fake.calls  # by its id, once found by its exact name


def dispatch(tmp_path: Path, command: str, installed: tuple[str, ...]) -> subprocess.CompletedProcess:
    base = tmp_path / "act"
    base.mkdir(exist_ok=True)
    for a in installed:
        (base / a).write_text("")
    fake_sudo = tmp_path / "sudo"
    fake_sudo.write_text('#!/bin/sh\necho "sudo $*"\n')
    fake_sudo.chmod(0o755)
    code = (Path(actor.__file__).parent / "scripts" / "act-dispatch").read_text()
    code = code.replace('BASE = "/usr/local/lib/kastellan/act"', f"BASE = {str(base)!r}")
    code = code.replace('("/usr/bin/sudo", "/bin/sudo", "/usr/local/bin/sudo")', f"({str(fake_sudo)!r},)")
    script = tmp_path / "dispatch"
    script.write_text(code)
    env = {"PATH": "/usr/bin:/bin", "SSH_ORIGINAL_COMMAND": command}
    return subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, env=env)


def test_the_dispatcher_accepts_only_known_forms(tmp_path: Path) -> None:
    ok = dispatch(tmp_path, "update-container norn", ("update-container",))
    assert ok.returncode == 0 and ok.stdout.strip().endswith("update-container norn")
    assert "-n" in ok.stdout and " -I -S " in ok.stdout
    for bad, code in [
        ("reboot", 3),  # not installed
        ("update-container", 2),
        ("update-container a b c", 2),
        ("update-container a ../b", 2),
        ("update-container a -b", 2),
        ("update-container ;id", 2),
        ("update-container $(id)", 2),
        ("rm -rf /", 2),
        ("", 2),
    ]:
        r = dispatch(tmp_path, bad, ("update-container",))
        assert r.returncode == code, (bad, r.stdout, r.stderr)
        assert "sudo" not in r.stdout
    tagged = dispatch(tmp_path, "update-container norn 1.2.3", ("update-container",))
    assert tagged.returncode == 0 and tagged.stdout.strip().endswith("update-container norn 1.2.3")
    (tmp_path / "other").mkdir()
    assert dispatch(tmp_path / "other", "restart-container norn 1.2", ("restart-container",)).returncode == 2
    listing = dispatch(tmp_path, "abilities", ("update-container", "reboot"))
    assert listing.stdout.splitlines()[0] == "reboot update-container"


def test_older_scripts_are_told(tmp_path: Path) -> None:
    """The dispatcher hashes the scripts as installed (a heredoc: one newline at the end)."""
    base = tmp_path / "act"
    base.mkdir()
    (base / "dispatch").write_text(actor._script("act-dispatch").rstrip("\n") + "\n")
    (base / "reboot").write_text(actor.ability_script("reboot").rstrip("\n") + "\n")
    (base / "update-container").write_text("an older one\n")
    out = dispatch(tmp_path, "abilities", ()).stdout
    abilities, outdated = actor.parse_abilities(out)
    assert set(abilities) == {"reboot", "update-container"} and outdated == ["update-container"]
    # A host set up before the scripts said which they are: all of them are older.
    assert actor.parse_abilities("reboot\n")[1] == ["dispatch", "reboot"]


def test_ability_lists_use_each_targets_name_rules(tmp_path: Path) -> None:
    names = "123 12345678-1234-1234-1234-123456789abc guest+one app"
    allow = tmp_path / "allow"
    allow.mkdir()
    for ability in ("start-container", "vm-start"):
        (allow / f"{ability}.allow").write_text(names.replace(" ", "\n") + "\n")
    code = actor._script("act-dispatch").replace("/etc/kastellan/act/", str(allow) + "/")
    base = tmp_path / "act"
    base.mkdir()
    for ability in ("start-container", "vm-start"):
        (base / ability).touch()
    code = code.replace('BASE = "/usr/local/lib/kastellan/act"', f"BASE = {str(base)!r}")
    r = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        env={"SSH_ORIGINAL_COMMAND": "abilities"},
    )
    assert r.returncode == 0, r.stderr
    found, _ = actor.parse_abilities(r.stdout)
    assert found["start-container"] == ["123", "12345678-1234-1234-1234-123456789abc", "app"]
    assert found["vm-start"] == ["guest+one", "app"]


async def test_an_untargeted_action_refuses_a_target() -> None:
    with pytest.raises(ValueError, match="takes no target"):
        await actor.act(None, None, "reboot", "app")


def test_the_install_script() -> None:
    script = actor.install_script({"update-container", "reboot"}, ["norn", "grundriss"], KEY, "192.168.15.50")
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    assert 'kastellan-act ALL=(root) NOPASSWD: $py -I -S /usr/local/lib/kastellan/act/reboot ""' in script
    assert "NOPASSWD: $py -I -S /usr/local/lib/kastellan/act/update-container *" in script
    assert "rm -f /usr/local/lib/kastellan/act/restart-container" in script  # not chosen: removed
    assert "printf '%s\\n' norn grundriss > /etc/kastellan/act/update-container.allow.new" in script
    nothing = actor.install_script(set(), [], KEY, None)
    assert "rm -f /etc/sudoers.d/kastellan-act" in nothing
    with pytest.raises(ValueError):
        actor.install_script({"rm-rf"}, [], KEY, None)
    with pytest.raises(ValueError):
        actor.install_script({"update-container"}, ["a;b"], KEY, None)
    for ability in actor.ABILITIES:
        compile(actor.ability_script(ability), ability, "exec")


def test_what_update_refuses(host) -> None:
    fake, run = host
    c = fake.find("app")
    # A locally built image (never pulled): not replaced by whatever the registry has.
    fake.image_info["sha256:" + "a" * 64]["RepoDigests"] = []
    r = run("update-container", "app")
    assert r.returncode == 1 and "wasn't pulled" in r.stderr
    fake.image_info["sha256:" + "a" * 64]["RepoDigests"] = ["me/app@sha256:" + "1" * 64]
    c["HostConfig"]["AutoRemove"] = True
    assert "--rm" in run("update-container", "app").stderr
    c["HostConfig"]["AutoRemove"] = False
    # A list entry that is only the start of another container's id isn't that container.
    assert "no container called cc" in run("update-container", "cc", allow="cc\n").stderr
    assert "no container called cc" in run("restart-container", "cc", allow="cc\n").stderr
    assert not any("restart" in x for x in fake.calls)


def test_a_crash_loop_and_a_disabled_healthcheck(host) -> None:
    fake, run = host
    fake.crash_loop = True
    r = run("update-container", "app")
    assert r.returncode == 1 and "crashes" in r.stderr and fake.find("app")["Id"] == "c" * 64
    fake.crash_loop = False
    fake.containers["c" * 64]["Config"]["Healthcheck"] = {"Test": ["NONE"]}
    assert run("update-container", "app").returncode == 0  # no health check: steady is enough


def test_networks_and_mounts_carry_over(host) -> None:
    fake, run = host
    c = fake.find("app")
    c["HostConfig"]["NetworkMode"] = "proj_default"
    c["HostConfig"]["Mounts"] = [{"Type": "volume", "Target": "/cache"}]
    c["NetworkSettings"]["Networks"] = {
        "proj_backend": {"Aliases": ["app"]},
        "proj_default": {"IPAMConfig": {"IPv4Address": "172.20.0.5"}, "Aliases": ["app"]},
    }
    r = run("update-container", "app")
    assert r.returncode == 0, r.stderr
    spec = fake.created
    assert list(spec["NetworkingConfig"]["EndpointsConfig"]) == ["proj_default"]  # its own network first
    assert "POST /networks/proj_backend/connect" in fake.calls
    assert "POST /networks/proj_default/connect" not in fake.calls
    assert spec["HostConfig"]["Mounts"] == [{"Type": "volume", "Target": "/cache", "Source": "anon1"}]


def test_a_pull_error_in_one_line_counts(host) -> None:
    fake, run = host
    original = fake.handle

    def handle(method, path, body):
        if "/images/create" in path:
            return 200, {"error": "toomanyrequests: rate limit"}
        return original(method, path, body)

    fake.handle = handle
    r = run("update-container", "app")
    assert r.returncode == 1 and "toomanyrequests" in r.stderr


def test_installing_system_updates(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "apt-get").write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$0.log"\n'
        'case "$*" in *upgrade*) echo "Unpacking libc6:amd64 (2.39-1) over (2.39-0) ..."; '
        'echo "Setting up libc6:amd64 (2.39-1) ..."; echo "Setting up curl (8.5-2) ...";; esac\n'
    )
    (bin_dir / "apt-get").chmod(0o755)
    code = actor.ability_script("upgrade-packages")
    code = code.replace('path="/usr/bin:/bin"', f"path={str(bin_dir)!r}")
    code = code.replace('LOCK = "/run/kastellan-act.lock"', f"LOCK = {str(tmp_path / 'lock')!r}")
    code = code.replace('"/var/log/kastellan-upgrade.log"', repr(str(tmp_path / "upgrade.log")))
    script = tmp_path / "upgrade-packages"
    script.write_text(code)
    r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["upgraded"] == 2 and out["packages"] == ["curl", "libc6"]
    calls = (bin_dir / "apt-get.log").read_text().splitlines()
    assert (
        calls[0] == "update"
        and "--with-new-pkgs" in calls[1]
        and "Dpkg::Options::=--force-confold" in calls[1]
    )
    assert subprocess.run([sys.executable, "-I", str(script), "x"], capture_output=True).returncode == 2
    # apt busy: said plainly
    (bin_dir / "apt-get").write_text(
        "#!/bin/sh\necho 'E: Could not get lock /var/lib/dpkg/lock-frontend' >&2\nexit 100\n"
    )
    r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "apt is busy" in r.stderr


def test_one_action_at_a_time(host) -> None:
    import fcntl

    _, run = host
    with open(run.folder / "lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # an upgrade runs
        r = run("restart-container", "app")
    assert r.returncode == 1 and "another action runs" in r.stderr


def test_the_uninstall_scripts() -> None:
    for script in (actor.uninstall_script(), collector.uninstall_script()):
        assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    assert "rm -rf /usr/local/lib/kastellan/act /etc/kastellan/act" in actor.uninstall_script()
    assert (
        "rm -f /etc/sudoers.d/kastellan-collect /usr/local/lib/kastellan/collect"
        in collector.uninstall_script()
    )
    assert "userdel -r kastellan-act" in actor.uninstall_script()


def test_uninstall_does_not_unlink_through_a_users_home_as_root(tmp_path: Path) -> None:
    home, victim = tmp_path / "home", tmp_path / "victim"
    home.mkdir()
    victim.mkdir()
    key = victim / "authorized_keys"
    key.write_text("another user's keys\n")
    (home / ".ssh").symlink_to(victim, target_is_directory=True)
    marker = tmp_path / "removed"
    called = tmp_path / "su-called"
    commands = {
        "id": f'test ! -f "{marker}"',
        "getent": f'echo "kastellan-act:x:123:123::%s:/bin/sh" | sed "s|%s|{home}|"',
        # Deliberately do not perform the unlink: only the target user's su may attempt it.
        "su": f'printf "%s\\n" "$@" > "{called}"',
        "pkill": "exit 0",
        "sleep": "exit 0",
        "userdel": f'touch "{marker}"',
    }
    for name, body in commands.items():
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    r = subprocess.run(
        ["/bin/sh", "-e"],
        input="\n".join(collector.remove_user("kastellan-act")),
        text=True,
        capture_output=True,
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    assert r.returncode == 0, r.stderr
    assert key.read_text() == "another user's keys\n"
    assert "kastellan-act" in called.read_text() and str(home) in called.read_text()


def test_no_setup_runs_in_demo_mode(tmp_path: Path) -> None:
    """Demo devices are made up: a real machine at one of their addresses is never logged in to."""
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.settings import Settings

    with TestClient(create_app(Settings(config=tmp_path / "c.yaml", data=tmp_path, demo=True))) as c:
        body = {"login": "root", "password": "x", "port": 22, "elevate": "none"}
        device = c.get("/api/devices").json()[0]["id"]
        for path in ("collect/install", "collect/uninstall", "act/uninstall"):
            r = c.post(f"/api/devices/{device}/{path}", json=body, headers={"X-Kastellan": "1"})
            assert r.status_code in (403, 409), (path, r.status_code)


def test_the_docker_api_version_is_asked(host) -> None:
    """Docker 29 refuses API versions below 1.44, Docker 20 knows nothing past 1.41: the version
    is the daemon's newest, up to the one the scripts were checked against."""
    fake, run = host
    fake.api = "1.57"  # newer than the scripts know: they speak 1.52
    assert run("restart-container", "app").returncode == 0 and fake.spoke == (1, 52)
    fake.api, fake.min_api = "1.41", "1.12"
    assert run("restart-container", "app").returncode == 0 and fake.spoke == (1, 41)


def test_an_old_daemon_updates_too(host) -> None:
    """Docker 1.13 (API 1.25): no Mounts in HostConfig, a container-wide MAC address."""
    fake, run = host
    fake.api, fake.min_api = "1.25", "1.12"
    c = fake.find("app")
    c["Config"]["MacAddress"] = "02:42:ac:11:00:09"
    c["HostConfig"].pop("Mounts", None)
    r = run("update-container", "app")
    assert r.returncode == 0, r.stderr
    assert fake.spoke == (1, 25) and fake.created["MacAddress"] == "02:42:ac:11:00:09"


def test_the_mac_address_goes_to_the_network(host) -> None:
    """From API 1.44 the MAC address is the network endpoint's (create refuses it from 1.52)."""
    fake, run = host
    c = fake.find("app")
    c["Config"]["MacAddress"] = "02:42:ac:11:00:09"
    c["HostConfig"]["NetworkMode"] = "bridge"
    c["NetworkSettings"]["Networks"] = {"bridge": {}}
    assert run("update-container", "app").returncode == 0
    assert "MacAddress" not in fake.created
    assert fake.created["NetworkingConfig"]["EndpointsConfig"]["bridge"]["MacAddress"] == "02:42:ac:11:00:09"


def test_a_pull_error_only_in_its_detail_counts(host) -> None:
    """API 1.48 on: "error" may be empty, "errorDetail" says it."""
    fake, run = host
    original = fake.handle

    def handle(method, path, body):
        if "/images/create" in path:
            return 200, {"errorDetail": {"message": "manifest unknown"}}
        return original(method, path, body)

    fake.handle = handle
    r = run("update-container", "app")
    assert r.returncode == 1 and "manifest unknown" in r.stderr


async def test_steps_come_as_they_go(tmp_path: Path) -> None:
    """Over SSH: steps reach the hub one by one; the answer is the last stdout line; an older
    script without steps still works; a failure's reason is its last line that isn't a step."""
    import asyncssh

    from kastellan.config import Device
    from kastellan.ssh import Keys

    keys = Keys(tmp_path / "ssh")
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    async def handle(process: asyncssh.SSHServerProcess) -> None:
        command = process.command or ""
        if command == "restart-container app":
            for text in ("restarting app", "checking it runs"):
                process.stderr.write(f"KASTELLAN-STEP {text}\n")
            process.stdout.write('{"container": "app", "restarted": true}\n')
            process.exit(0)
        elif command == "rollback-container app":
            process.stdout.write('{"rolled_back": true}\n')
            process.exit(0)
        elif command == "reboot":  # an older script: no steps
            process.stdout.write('{"reboot": "in 1 minute"}\n')
            process.exit(0)
        else:
            process.stderr.write("KASTELLAN-STEP pulling me/app:1\napp: pull: manifest unknown\n")
            process.exit(1)

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

    server = await asyncssh.create_server(
        Server,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        authorized_client_keys=asyncssh.import_authorized_keys(keys.public_key("act")),
        process_factory=handle,
    )
    port = server.sockets[0].getsockname()[1]
    try:
        device = Device.model_validate({"host": "127.0.0.1", "act": {"port": port}})
        await keys.trust(actor.target_of(device), host_key.get_fingerprint())
        seen: list[str] = []
        result = await actor.act(keys, device, "restart-container", "app", on_step=seen.append)
        assert result == {"container": "app", "restarted": True}
        assert seen == ["restarting app", "checking it runs"]
        assert await actor.act(keys, device, "reboot") == {"reboot": "in 1 minute"}
        assert await actor.act(keys, device, "rollback-container", "app") == {"rolled_back": True}
        with pytest.raises(RuntimeError, match=r"^app: pull: manifest unknown$"):
            await actor.act(keys, device, "update-container", "app", on_step=seen.append)
        assert seen[-1] == "pulling me/app:1"
    finally:
        server.close()


def test_the_streamed_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Steps, then the outcome; audited when it starts and when it ends."""
    from fastapi.testclient import TestClient

    from kastellan import act_api
    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    async def fake_act(keys, device, ability, container=None, on_step=None, tag=None):
        on_step("pulling me/app:1")
        on_step("creating the new container")
        if container == "broken":
            raise RuntimeError("the new container didn't work: it restarted (it crashes)")
        return {"container": container, "updated": True, "from": "a", "to": "b"}

    monkeypatch.setattr(act_api.actor, "act", fake_act)
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n    act: {}\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('pw for tests')}\n"
    )
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=h)
        r = c.post("/api/devices/nas/act/update-container/stream", json={"container": "app"}, headers=h)
        lines = r.text.splitlines()
        assert lines[:2] == ["KASTELLAN-STEP pulling me/app:1", "KASTELLAN-STEP creating the new container"]
        assert json.loads(lines[-1].removeprefix("KASTELLAN-DONE ")) == {
            "ok": True,
            "result": {"container": "app", "updated": True, "from": "a", "to": "b"},
        }
        bad = c.post("/api/devices/nas/act/update-container/stream", json={"container": "broken"}, headers=h)
        outcome = json.loads(bad.text.splitlines()[-1].removeprefix("KASTELLAN-DONE "))
        assert outcome == {"ok": False, "error": "the new container didn't work: it restarted (it crashes)"}
    audit = [json.loads(ln) for ln in (tmp_path / "audit.log").read_text().splitlines()]
    acts = [a["outcome"] for a in audit if a.get("action") == "act.update-container"]
    assert acts == [
        "started",
        "done",
        "started",
        "failed: the new container didn't work: it restarted (it crashes)",
    ]


def test_no_reboot_while_apt_works(tmp_path: Path) -> None:
    """An upgrade the actor gave up waiting for goes on by itself: a reboot waits for it."""
    import fcntl

    lock = tmp_path / "lock-frontend"
    lock.write_text("")
    code = actor.ability_script("reboot")
    code = code.replace('"/var/lib/dpkg/lock-frontend"', repr(str(lock)))
    code = code.replace('LOCK = "/run/kastellan-act.lock"', f"LOCK = {str(tmp_path / 'act.lock')!r}")
    script = tmp_path / "reboot"
    script.write_text(code)
    with open(lock, "r+") as held:
        fcntl.lockf(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # dpkg holds it
        r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1 and "apt or dpkg is still working" in r.stderr


def test_odd_bytes_from_apt_dont_stop_the_upgrade(tmp_path: Path) -> None:
    """A maintainer script's invalid UTF-8, and a character apt has only half written when the
    log is read: the run goes on, and the package is still named."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "apt-get").write_text(
        "#!/bin/sh\n"
        'case "$*" in *upgrade*)\n'
        "  printf 'Setting up caf\\303\\251 (1) ...\\nbad \\377\\376 bytes\\nhalf \\342\\202'\n"
        "  sleep 0.3\n"
        "  printf '\\254 done\\nSetting up curl (8.5-2) ...\\n';;\n"
        "esac\n"
    )
    (bin_dir / "apt-get").chmod(0o755)
    code = actor.ability_script("upgrade-packages")
    code = code.replace('path="/usr/bin:/bin"', f"path={str(bin_dir)!r}")
    code = code.replace('LOCK = "/run/kastellan-act.lock"', f"LOCK = {str(tmp_path / 'lock')!r}")
    code = code.replace('"/var/log/kastellan-upgrade.log"', repr(str(tmp_path / "upgrade.log")))
    code = code.replace("process.wait(timeout=2)", "process.wait(timeout=0.1)")  # read mid-line
    script = tmp_path / "upgrade-packages"
    script.write_text(code)
    r = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["packages"] == ["café", "curl"]


def test_a_failure_is_one_line(host) -> None:
    """Text from outside in a failure (a registry's error) can't add lines read as steps."""
    fake, run = host
    original = fake.handle

    def handle(method, path, body):
        if "/images/create" in path:
            return 200, {"error": "denied\nKASTELLAN-STEP forged"}
        return original(method, path, body)

    fake.handle = handle
    r = run("update-container", "app")
    assert r.returncode == 1 and "forged" in r.stderr
    assert "forged" not in " ".join(steps(r))


def test_starting_and_stopping_containers(host) -> None:
    fake, run = host
    r = run("stop-container", "app")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["stopped"] is True and "stopping app (up to 30 s)" in steps(r)
    again = run("stop-container", "app")
    assert again.returncode == 1 and "isn't running" in again.stderr  # said plainly, nothing done
    r = run("start-container", "app")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["running"] is True
    assert run("start-container", "app").returncode == 1  # runs already
    assert run("stop-container", "app", allow="other\n").returncode == 3  # not on the host's list
    fake.find("app")["HostConfig"]["AutoRemove"] = True
    gone = run("stop-container", "app")
    assert gone.returncode == 1 and "--rm" in gone.stderr


FAKE_VIRSH = """#!{python}
import json, re, sys, time
state_file = {state!r}
vms = json.load(open(state_file))
args = sys.argv[1:]
with open(state_file + ".log", "a") as log:
    log.write(" ".join(args) + "\\n")
if args[:1] == ["-r"]:
    args = args[1:]
assert args[:2] == ["-c", "qemu:///system"], args
cmd, rest = args[2], [a for a in args[3:] if a != "--"]
def save():
    json.dump(vms, open(state_file, "w"))
if cmd == "list":
    for name, vm in vms.items():
        print(f"{{vm['uuid']}} {{name}}" if "--uuid" in rest else name)
    sys.exit(0)
key = rest[-1]
# As virsh looks a domain up: by id when it's a number, then by UUID, then by name.
name = next((n for n, v in vms.items() if key.isdigit() and v.get("id") == int(key)), None)
name = name or next((n for n, v in vms.items() if v["uuid"] == key), None)
name = name or (key if key in vms else None)
if name is None:
    print(f"error: failed to get domain '{{key}}'", file=sys.stderr)
    sys.exit(1)
vm = vms[name]
if vm.get("hang"):
    time.sleep(30)
if cmd == "domstate":
    print(vm["state"])
elif cmd == "dominfo":
    print(f"Name:           {{name}}\\nUUID:           {{vm['uuid']}}\\nState:          {{vm['state']}}\\n"
          f"CPU(s):         2\\nMax memory:     2097152 KiB\\nUsed memory:    2097152 KiB\\n"
          f"Persistent:     yes\\nAutostart:      {{'enable' if vm.get('autostart') else 'disable'}}")
elif cmd == "domblklist":
    print(" Type   Device   Target   Source\\n" + "-" * 40)
    print(f" file   disk     vda      /var/lib/libvirt/images/{{name}}.qcow2")
    print(" file   cdrom    sda      -")
elif cmd in ("start", "resume"):
    vm["state"] = "running"
elif cmd == "shutdown":
    if vm.get("acpi", True):
        vm["state"] = "shut off"
elif cmd == "reboot":
    pass
elif cmd == "destroy":
    vm["state"] = "shut off"
save()
"""


U1, U2, U3, U4 = (f"0000000{i}-1111-2222-3333-444444444444" for i in range(1, 5))


@pytest.fixture
def libvirt(tmp_path: Path):
    """A fake virsh with VMs kept in a file, and a runner for the VM abilities against it."""
    state = tmp_path / "vms.json"
    state.write_text(
        json.dumps(
            {
                "win11": {"state": "running", "autostart": True, "uuid": U1, "id": 1},
                "deb+test": {"state": "shut off", "uuid": U2},
                "stubborn": {"state": "running", "acpi": False, "uuid": U3, "id": 2},
                "frozen": {"state": "running", "hang": True, "uuid": U4, "id": 3},
            }
        )
    )
    virsh = tmp_path / "virsh"
    virsh.write_text(FAKE_VIRSH.format(python=sys.executable, state=str(state)))
    virsh.chmod(0o755)
    (tmp_path / "allow").mkdir()

    def with_virsh(code: str) -> str:
        return code.replace(
            'VIRSH = ("/usr/bin/virsh", "/usr/sbin/virsh", "/bin/virsh")', f"VIRSH = ({str(virsh)!r},)"
        )

    def run(ability: str, *args: str, allow: str = "*\n") -> subprocess.CompletedProcess:
        (tmp_path / "allow" / f"{ability}.allow").write_text(allow)
        code = with_virsh(actor.ability_script(ability))
        code = code.replace("/etc/kastellan/act/", f"{tmp_path}/allow/")
        code = code.replace('LOCK = "/run/kastellan-act.lock"', f"LOCK = {str(tmp_path / 'lock')!r}")
        code = code.replace(", 180)", ", 0.3)").replace("every=5", "every=0.05")  # no minutes in tests
        code = code.replace("timeout=20)", "timeout=1)")  # a hanging virsh: a second, not 20
        script = tmp_path / ability
        script.write_text(code)
        return subprocess.run(
            [sys.executable, "-I", str(script), *args], capture_output=True, text=True, timeout=60
        )

    run.state = lambda: json.loads(state.read_text())  # type: ignore[attr-defined]
    run.log = lambda: (tmp_path / "vms.json.log").read_text().splitlines()  # type: ignore[attr-defined]
    run.with_virsh = with_virsh  # type: ignore[attr-defined]
    return run


def test_vms_are_started_shut_down_and_forced_off(libvirt) -> None:
    run = libvirt
    r = run("vm-start", "deb+test")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["state"] == "running" and run.state()["deb+test"]["state"] == "running"
    assert run("vm-start", "deb+test").returncode == 1  # runs already
    r = run("vm-shutdown", "win11")
    assert r.returncode == 0 and run.state()["win11"]["state"] == "shut off"
    assert "asking win11 to shut down (ACPI)" in steps(r)
    stubborn = run("vm-shutdown", "stubborn")  # a guest that ignores ACPI: it says so, nothing forced
    assert stubborn.returncode == 1 and "still running" in stubborn.stderr
    assert run.state()["stubborn"]["state"] == "running"
    r = run("vm-force-off", "stubborn")
    assert r.returncode == 0 and json.loads(r.stdout)["forced"] is True
    assert run.state()["stubborn"]["state"] == "shut off"
    assert run("vm-reboot", "stubborn").returncode == 1  # not running
    # Acted on by its UUID, never by a name virsh could read as another domain's id.
    assert f"-c qemu:///system destroy -- {U3}" in run.log()
    assert not any(line.endswith(" stubborn") for line in run.log() if " destroy " in line)
    assert run("vm-start", "win11", allow="deb+test\n").returncode == 3  # not on the host's list
    assert run("vm-start", "nope").returncode == 1  # libvirt knows no such VM
    for bad in ("-c", "a b", "x;y", "", "2", U1, U1.upper()):  # a number or a UUID: never a name
        assert run("vm-start", bad).returncode == 2, bad


def test_a_restart_is_only_asked_for(libvirt) -> None:
    """libvirt can't tell whether the guest restarted: the answer says it was asked."""
    r = libvirt("vm-reboot", "win11")
    assert r.returncode == 0 and json.loads(r.stdout)["asked"] is True
    assert any("libvirt can't tell" in s for s in steps(r))


def test_a_hanging_virsh_is_one_line(libvirt) -> None:
    """libvirtd stuck, or the domain in a job: a short failure, not a traceback."""
    r = libvirt("vm-shutdown", "frozen")
    assert r.returncode == 1
    lines = [ln for ln in r.stderr.splitlines() if not ln.startswith("KASTELLAN-STEP")]
    assert len(lines) == 1 and "didn't answer" in lines[0], r.stderr


def test_the_collector_lists_vms(libvirt) -> None:
    code = libvirt.with_virsh(collector.script()).replace("timeout=10)", "timeout=1)")
    data = json.loads(
        subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True).stdout
    )
    by_name = {v["name"]: v for v in data["vms"]}
    assert set(by_name) == {"win11", "deb+test", "stubborn"}  # the stuck one is left out, and said
    assert "frozen" in data["errors"]["vms"]
    assert by_name["win11"]["uuid"] == U1
    assert all(line.startswith("-r ") for line in libvirt.log())  # read-only, every call
    assert not any(line.endswith(" win11") for line in libvirt.log())  # asked by UUID
    win = by_name["win11"]
    assert win["state"] == "running" and win["autostart"] is True and win["vcpus"] == 2
    assert win["memory"] == 2 * 1024**3
    assert win["disks"] == [{"target": "vda", "source": "/var/lib/libvirt/images/win11.qcow2"}]  # no cdrom


def test_the_dispatcher_knows_the_new_forms(tmp_path: Path) -> None:
    for command, installed in [
        ("start-container app", ("start-container",)),
        ("stop-container app", ("stop-container",)),
        ("vm-shutdown deb+test", ("vm-shutdown",)),
        ("vm-force-off win11", ("vm-force-off",)),
    ]:
        ok = dispatch(tmp_path, command, installed)
        assert ok.returncode == 0 and ok.stdout.strip().endswith(command.split(" ", 1)[1]), command
    assert dispatch(tmp_path, "vm-start a;b", ("vm-start",)).returncode == 2
    assert dispatch(tmp_path, "vm-start 12", ("vm-start",)).returncode == 2  # a domain id
    assert dispatch(tmp_path, f"vm-start {U1}", ("vm-start",)).returncode == 2  # a UUID
    assert dispatch(tmp_path, "vm-start", ("vm-start",)).returncode == 2


def test_the_install_script_lists_vms() -> None:
    script = actor.install_script(
        {"vm-start", "stop-container"}, ["app"], KEY, None, vms=["win11", "deb+test"]
    )
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    assert "printf '%s\\n' win11 deb+test > /etc/kastellan/act/vm-start.allow.new" in script
    assert "printf '%s\\n' app > /etc/kastellan/act/stop-container.allow.new" in script
    with pytest.raises(ValueError):
        actor.install_script({"vm-start"}, [], KEY, None, vms=["a;b"])
    for ability in actor.ABILITIES:
        compile(actor.ability_script(ability), ability, "exec")


def test_the_collector_stops_asking_libvirt_in_time(libvirt) -> None:
    """All VMs within VMS_WITHIN: past it, the rest are skipped (and named), the report goes on."""
    code = libvirt.with_virsh(collector.script()).replace("VMS_WITHIN = 25", "VMS_WITHIN = 0")
    data = json.loads(
        subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True).stdout
    )
    assert data["vms"] == [] and "win11" in data["errors"]["vms"]
    assert "raid" in data  # the other sections are all there


def test_an_action_takes_what_its_ability_acts_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A VM ability takes `vm`, a container ability `container`: the other field is refused, and
    the audit names what was acted on."""
    from fastapi.testclient import TestClient

    from kastellan import act_api
    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    asked: list[tuple[str, str | None]] = []

    async def fake_act(keys, device, ability, name=None, on_step=None, tag=None):
        if ability == "journal":
            return {"lines": []}
        asked.append((ability, name))
        return {"ok": True}

    monkeypatch.setattr(act_api.actor, "act", fake_act)
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n    act: {}\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('pw for tests')}\n"
    )
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=h)
        url = "/api/devices/nas/act"
        assert c.post(f"{url}/restart-container", json={"vm": "x"}, headers=h).status_code == 400
        assert c.post(f"{url}/vm-start", json={"container": "x"}, headers=h).status_code == 400
        both = {"vm": "x", "container": "y"}
        assert c.post(f"{url}/vm-start", json=both, headers=h).status_code == 400
        assert c.post(f"{url}/vm-start", json={"vm": "win11"}, headers=h).status_code == 200
        assert c.post(f"{url}/restart-container", json={"container": "app"}, headers=h).status_code == 200
    assert asked == [("vm-start", "win11"), ("restart-container", "app")]
    audit = [json.loads(ln) for ln in (tmp_path / "audit.log").read_text().splitlines()]
    acts = {a["action"]: a for a in audit if a.get("action", "").startswith("act.")}
    assert acts["act.vm-start"]["vm"] == "win11" and "container" not in acts["act.vm-start"]
    assert acts["act.restart-container"]["container"] == "app"


def test_reading_a_containers_log(host) -> None:
    fake, run = host
    r = run("container-logs", "app", allow="app\n")
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["lines"] == ["2026-11-21T10:00:00Z hello", "2026-11-21T10:00:01Z error: oops"]
    assert result["running"] is True
    assert not any(c.startswith(("POST", "DELETE")) for c in fake.calls)  # reads only
    assert run("container-logs", "other", allow="app\n").returncode == 3  # not on the list


def test_reading_a_units_journal(host, tmp_path: Path) -> None:
    _, run = host
    journal = tmp_path / "journalctl"
    journal.write_text(
        f"#!{sys.executable}\nimport sys\nprint('args', *sys.argv[1:])\nprint('\\x1b[1mbold\\x1b[0m line')\n"
    )
    journal.chmod(0o755)
    folder = run.folder  # type: ignore[attr-defined]
    code = actor.ability_script("unit-logs").replace(
        '"/usr/bin/journalctl", "/bin/journalctl"', f"{str(journal)!r},"
    )
    code = code.replace("/etc/kastellan/act/", f"{folder}/allow/")
    (folder / "allow" / "unit-logs.allow").write_text("*\n")
    script = folder / "unit-logs"
    script.write_text(code)

    def logs(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(script), *args], capture_output=True, text=True, timeout=30
        )

    r = logs("nginx.service")
    assert r.returncode == 0, r.stderr
    lines = json.loads(r.stdout)["lines"]
    assert lines == ["args --no-pager --quiet -o short-iso -n 300 -u nginx.service", "bold line"]
    for bad in ("nginx", "-u.service", "a b.service", "x;y.service"):
        assert logs(bad).returncode == 2, bad


async def test_log_lines_stay_out_of_the_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n    act: {user: kastellan-act}\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('admin password')}\n"
    )

    async def fake_act(keys, device, ability, name=None, on_step=None, tag=None):
        assert (ability, name) == ("unit-logs", "nginx.service")
        return {"unit": name, "lines": ["SECRET=hunter2"], "state": "failed"}

    monkeypatch.setattr(actor, "act", fake_act)
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        c.post("/api/auth/login", json={"user": "ada", "password": "admin password"}, headers=h)
        r = c.post("/api/devices/nas/act/unit-logs", json={"unit": "nginx.service"}, headers=h)
        assert r.status_code == 200 and r.json()["lines"] == ["SECRET=hunter2"]
        bad = c.post("/api/devices/nas/act/unit-logs", json={"container": "nginx.service"}, headers=h)
        assert bad.status_code == 400
    audit = (tmp_path / "audit.log").read_text()
    assert "act.unit-logs" in audit and "hunter2" not in audit


def test_renewal_preserves_each_abilitys_targets() -> None:
    allowed = {
        "start-container": ["app"],
        "stop-container": ["other"],
        "container-logs": ["log"],
        "unit-logs": ["nginx.service"],
        "vm-start": ["vm+1"],
        "reboot": [],
    }
    script = actor.install_script(set(allowed), ["*"], KEY, None, vms=["*"], allowed=allowed)
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    for ability, names in allowed.items():
        if names:
            assert f"printf '%s\\n' {names[0]} > /etc/kastellan/act/{ability}.allow.new" in script
    for bad in ({"start-container": ["app;id"]}, {"reboot": ["app"]}, {"unit-logs": ["../shadow"]}):
        with pytest.raises(ValueError):
            actor.install_script(set(bad), [], KEY, None, allowed=bad)
    with pytest.raises(ValueError):
        actor.install_script({"start-container"}, [], KEY, None, allowed={})


def test_inherited_healthcheck_is_respected(host) -> None:
    fake, run = host
    health = {"Test": ["CMD", "check"]}
    fake.image_info["sha256:" + "a" * 64]["Config"]["Healthcheck"] = health
    fake.image_info["sha256:" + "b" * 64]["Config"]["Healthcheck"] = health
    fake.find("app")["Config"]["Healthcheck"] = health
    original = fake.handle

    def handle(method, path, body):
        status, result = original(method, path, body)
        if (
            method == "GET"
            and path.endswith("/json")
            and isinstance(result, dict)
            and result.get("Id") == fake.created_id
        ):
            result["Config"] = {**result["Config"], "Healthcheck": health}  # inherited by Docker
            result["State"]["Health"] = {"Status": "unhealthy"}
        return status, result

    fake.handle = handle
    r = run("update-container", "app")
    assert r.returncode == 1 and "unhealthy" in r.stderr
    assert fake.find("app")["Image"] == "sha256:" + "a" * 64
    assert "Healthcheck" not in fake.created  # omitted because it matches the image default


def test_log_json_stays_bounded_for_unicode_and_escapes(host) -> None:
    fake, run = host
    original = fake.handle
    fake.find("app")["Config"]["Tty"] = True

    def handle(method, path, body):
        if "/logs?" in path:
            return 200, ((('é漢"\\' * 100) + "\n") * 300).encode()
        return original(method, path, body)

    fake.handle = handle
    r = run("container-logs", "app")
    assert r.returncode == 0 and len(r.stdout.encode()) < 65536
    assert json.loads(r.stdout)["lines"]


def test_log_contents_never_reach_host_syslog(monkeypatch, capsys) -> None:
    import syslog

    seen = []
    monkeypatch.setattr(syslog, "syslog", lambda level, text: seen.append(text))
    namespace = {}
    exec(actor._script("act-common"), namespace)
    with pytest.raises(SystemExit):
        namespace["done"]({"unit": "nginx.service", "lines": ["secret=hunter2"]})
    assert "hunter2" in capsys.readouterr().out
    assert "hunter2" not in str(seen) and "nginx.service" in str(seen)


def test_reading_the_journal_after_a_cursor(host, tmp_path: Path) -> None:
    _, run = host
    journal = tmp_path / "journalctl"
    entries = [
        {
            "__CURSOR": "s=1;i=1",
            "__REALTIME_TIMESTAMP": "1700000000000000",
            "PRIORITY": "6",
            "SYSLOG_IDENTIFIER": "sshd",
            "MESSAGE": "Accepted publickey for ada",
        },
        {
            "__CURSOR": "s=1;i=2",
            "__REALTIME_TIMESTAMP": "1700000001000000",
            "PRIORITY": "3",
            "_COMM": "dockerd",
            "CONTAINER_NAME": "app",
            "MESSAGE": [104, 105, 27, 91, 49, 109],
        },
    ]
    journal.write_text(
        f"#!{sys.executable}\nimport sys, json\n"
        "open(sys.argv[0] + '.args', 'w').write(' '.join(sys.argv[1:]))\n"
        f"for e in {entries!r}:\n    print(json.dumps(e))\n"
    )
    journal.chmod(0o755)
    folder = run.folder  # type: ignore[attr-defined]
    code = actor.ability_script("journal").replace(
        '"/usr/bin/journalctl", "/bin/journalctl"', f"{str(journal)!r},"
    )
    script = folder / "journal"
    script.write_text(code)

    def read(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(script), *args], capture_output=True, text=True, timeout=30
        )

    r = read("-")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["cursor"] == "s=1;i=2"
    assert [(x["program"], x["level"], x["container"]) for x in out["lines"]] == [
        ("sshd", 6, ""),
        ("dockerd", 3, "app"),
    ]
    assert out["lines"][1]["msg"].startswith("hi")
    assert "-n 500" in (tmp_path / "journalctl.args").read_text()
    assert read("s=1;i=2").returncode == 0
    assert "--after-cursor=s=1;i=2" in (tmp_path / "journalctl.args").read_text()
    for bad in ("", "a b", "x;y$(id)", "--since=1"):
        assert read(bad).returncode == 2, bad
    assert "journal" in actor.READING
    script_text = actor.install_script({"journal"}, [], KEY, None)
    assert "NOPASSWD: $py -I -S /usr/local/lib/kastellan/act/journal *" in script_text


def test_every_containers_log_since_a_time(host) -> None:
    fake, run = host
    r = run("docker-logs", "-", allow="")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert [(x["container"], x["stderr"], x["msg"]) for x in out["lines"]] == [
        ("app", False, "hello"),
        ("app", True, " error : oops"),
    ]
    assert out["since"] == "1795255201.000000000"
    again = json.loads(run("docker-logs", out["since"], allow="").stdout)
    assert again["lines"] == [] and again["since"] == out["since"]  # nothing newer
    fake.find("app")["HostConfig"] = {"LogConfig": {"Type": "journald"}}  # comes with the journal
    assert json.loads(run("docker-logs", "-", allow="").stdout)["lines"] == []
    for bad in ("", "1.2.3", "now", "1;id"):
        assert run("docker-logs", bad, allow="").returncode == 2, bad


async def test_large_journal_batches_are_not_truncated() -> None:
    import asyncio
    from types import SimpleNamespace

    from kastellan.config import Device

    result = {"lines": [{"msg": "x" * 1000}] * 200, "cursor": "s=1;i=200"}
    payload = json.dumps(result)

    class Output:
        async def readexactly(self, n):
            assert n > len(payload) > 65536
            raise asyncio.IncompleteReadError(payload, n)

    async def errors():
        for line in []:
            yield line

    async def wait(**kw):
        return SimpleNamespace(exit_status=0)

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create_process(self, command, **kw):
            assert command == "journal s=1;i=100"
            return SimpleNamespace(stdout=Output(), stderr=errors(), wait=wait)

    class Keys:
        async def connect(self, *args):
            return Connection()

    assert await actor.act(Keys(), Device(host="host", act={}), "journal", "s=1;i=100") == result


def test_docker_log_batches_keep_the_earliest_unread_entries(host) -> None:
    from urllib.parse import parse_qs, urlsplit

    fake, run = host
    original = fake.handle
    fake.find("app")["Config"]["Tty"] = True

    def handle(method, path, body):
        if "/logs?" in path:
            query = parse_qs(urlsplit(path).query)
            assert query["tail"] == ["all"]
            after = float(query["since"][0]) - 1795255200
            return 200, b"".join(
                f"2026-11-21T10:00:{i // 100:02d}.{i % 100:02d}0000000Z entry {i}\n".encode()
                for i in range(2500)
                if i / 100 >= after
            )
        return original(method, path, body)

    fake.handle = handle
    first = json.loads(run("docker-logs", "1795255199", allow="").stdout)
    second = json.loads(run("docker-logs", first["since"], allow="").stdout)
    assert [ln["msg"] for ln in first["lines"] + second["lines"]] == [f"entry {i}" for i in range(2500)]
    fake.handle = lambda method, path, body: (
        (500, b"failed") if "/logs?" in path else original(method, path, body)
    )
    assert run("docker-logs", second["since"], allow="").returncode != 0


def test_only_a_tag_the_hub_found_and_not_needed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag goes to the host only when the hub found it for that container; an update offer
    said to be not needed stops being shown until a newer one comes."""
    from fastapi.testclient import TestClient

    from kastellan import act_api
    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.monitor import DeviceStatus
    from kastellan.settings import Settings

    asked: list[tuple[str, str | None, str | None]] = []

    async def fake_act(keys, device, ability, name=None, on_step=None, tag=None):
        asked.append((ability, name, tag))
        return {"updated": True}

    monkeypatch.setattr(act_api.actor, "act", fake_act)
    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\n    act: {}\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('pw for tests')}\n"
    )
    h = {"X-Kastellan": "1"}
    bind = {"key": "bind", "name": "bind", "image": "isc/bind9", "tag": "9.20", "update": True}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=h)
        hub = c.app.state.hub
        hub.monitor.status["nas"] = DeviceStatus(
            containers=[{**bind, "update_to": "10.0", "update_within": "9.21", "update_digest": ""}]
        )
        url = "/api/devices/nas/act/update-container"
        for tag in ("9.21", "10.0"):
            assert c.post(url, json={"container": "bind", "tag": tag}, headers=h).status_code == 200
        assert c.post(url, json={"container": "bind", "tag": "9.19"}, headers=h).status_code == 409
        assert c.post(url, json={"container": "other", "tag": "9.21"}, headers=h).status_code == 409
        container = hub.monitor.status["nas"].containers[0]
        container["unchecked"] = "registry unavailable"
        assert c.post(url, json={"container": "bind", "tag": "10.0"}, headers=h).status_code == 409
        container["unchecked"] = None
        hub.monitor.status["nas"].containers.append({**container, "key": "remote/bind"})
        assert c.post(url, json={"container": "bind", "tag": "10.0"}, headers=h).status_code == 409
        hub.monitor.status["nas"].containers.pop()
        restart = "/api/devices/nas/act/restart-container"
        assert c.post(restart, json={"container": "bind", "tag": "9.21"}, headers=h).status_code == 400
        assert asked == [("update-container", "bind", "9.21"), ("update-container", "bind", "10.0")]

        def shown():
            (d,) = [d for d in c.get("/api/devices").json() if d["id"] == "nas"]
            return d["containers"][0]["skipped"]

        assert shown() is False
        assert c.post("/api/devices/nas/containers/bind/not-needed", headers=h).json() == {"skipped": "10.0"}
        assert shown() is True
        hub.monitor.status["nas"].containers[0]["update_to"] = "10.1"  # a newer offer shows again
        assert shown() is False
        assert c.post("/api/devices/nas/containers/nope/not-needed", headers=h).status_code == 404
    audit = [json.loads(ln) for ln in (tmp_path / "audit.log").read_text().splitlines()]
    assert any(a.get("action") == "act.update-container" and a.get("tag") == "10.0" for a in audit)
    assert any(a.get("action") == "update.not-needed" and a.get("update") == "10.0" for a in audit)


def test_switching_tags_even_when_the_image_is_identical(host) -> None:
    fake, run = host
    a = "sha256:" + "a" * 64
    fake.images = {"me/app:1": a, "me/app:2": a}
    result = run("update-container", "app", "2")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["updated"] is True
    assert fake.find("app")["Config"]["Image"] == "me/app:2"
    assert fake.find("app")["Image"] == a


def test_skipped_offers_belong_to_the_image_and_ignore_damaged_entries(tmp_path: Path) -> None:
    from kastellan.skipped import Skipped, identity

    path = tmp_path / "skipped.json"
    path.write_text('{"nas": [], "other": {"app": 42}}')
    skipped = Skipped(path)
    c = {"name": "app", "image": "repo/app", "tag": "1", "update": True, "update_to": "2"}
    assert not skipped.is_skipped("nas", c)
    skipped.skip("nas", "app", identity(c))
    assert skipped.is_skipped("nas", c)
    assert not skipped.is_skipped("nas", {**c, "image": "another/app"})
    assert not skipped.is_skipped("nas", {**c, "tag": "1.5"})
    assert not skipped.is_skipped("nas", {**c, "update_to": "3"})
