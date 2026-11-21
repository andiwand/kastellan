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
            return (
                (200, {"Id": self.images[ref]}) if ref in self.images else (404, {"message": "no such image"})
            )
        if method == "POST" and p == "/containers/create":
            spec = json.loads(body)
            self.created = spec
            cid = "d" * 64
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
            if method == "GET" and action == "json" and c["Id"] == "d" * 64 and self.crash_loop:
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
                works = c["Id"] != "d" * 64 or self.new_image_runs
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
            raw = json.dumps(data).encode() if data is not None else b""
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


def test_update_recreates_with_the_same_settings(host) -> None:
    fake, run = host
    r = run("update-container", "app")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {
        "container": "app",
        "image": "me/app:1",
        "updated": True,
        "from": "a" * 12,
        "to": "b" * 12,
    }
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
    assert r.returncode == 1 and "the old container runs again" in r.stderr
    old = fake.find("app")
    assert old["Id"] == "c" * 64 and old["State"]["Running"] and "d" * 64 not in fake.containers


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
    assert run("update-container", "app", "more").returncode == 2
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
        ("update-container a b", 2),
        ("update-container ;id", 2),
        ("update-container $(id)", 2),
        ("rm -rf /", 2),
        ("", 2),
    ]:
        r = dispatch(tmp_path, bad, ("update-container",))
        assert r.returncode == code, (bad, r.stdout, r.stderr)
        assert "sudo" not in r.stdout
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
