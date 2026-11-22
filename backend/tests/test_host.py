"""The collector (install, its report, the alarms from it) and registry update checks."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from kastellan import config_edit, docker
from kastellan.alarms import Severity, evaluate, host_problems
from kastellan.config import AlarmSettings, Config
from kastellan.host import collector
from kastellan.host.report import normalize
from kastellan.monitor import CheckStatus, DeviceStatus
from kastellan.registry import Registries, newer_tag, parse

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBgfPq3YGT1qT0EzQAp9VUdHPu+P2Q0Ck0tUjJmQNlpc kastellan-collect"


def test_the_install_script() -> None:
    script = collector.install_script(KEY, "192.168.15.50")
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    assert (
        'echo "kastellan-collect ALL=(root) NOPASSWD: $cmd"' in script
        and 'cmd="$py -I -S ' in script
        and "isn't root's alone" in script
    )
    assert 'openssh_line="${hub:+from=\\"$hub\\",}restrict,command=\\"sudo -n $cmd\\" $key"' in script
    assert "chown root:root /usr/local/lib/kastellan/collect.new" in script


def test_the_collector_runs_and_says_which_it_is(tmp_path: Path) -> None:
    """On any machine (here maybe without /proc): JSON, with what it couldn't read in errors.
    Installed through a heredoc (one newline at the end), it reports what the hub expects."""
    path = tmp_path / "collect"
    path.write_text(collector.script().rstrip("\n") + "\n")
    data = json.loads(subprocess.run([sys.executable, "-I", path], capture_output=True, text=True).stdout)
    assert data["version"] == 1 and isinstance(data["errors"], dict)
    assert not collector.outdated(normalize(data))
    assert collector.outdated(normalize(data | {"script": "older"}))
    assert collector.outdated(normalize({}))  # from before collectors said it
    assert not collector.outdated(None)  # no report: nothing to say


REPORT = {
    "filesystems": [
        {"mount": "/", "used": 90, "available": 10, "total": 100},
        {"mount": "/data", "used": 50, "available": 50, "total": 100},
        {"mount": "/full", "used": 97, "available": 3, "total": 100},
    ],
    "raid": [{"name": "md127", "active": True, "status": "U_", "failed": ["sdb"], "progress": None}],
    "smart": {
        "disks": [
            {"disk": "sda", "model": "TOSHIBA", "passed": True, "reallocated": 8, "temperature": 60},
            {"disk": "sdb", "passed": False},
            {"disk": "nvme0n1", "passed": True, "nvme_used_percent": 3, "temperature": 40},
        ]
    },
    "temperatures": [
        {"sensor": "psu", "celsius": 71, "limit": 70},
        {"sensor": "cpu", "celsius": 66, "limit": None},
    ],
    "services": {"failed": ["logrotate.service"]},
}


def test_host_problems() -> None:
    found = {p.key: (p.severity, p.title) for p in host_problems("sv1", "SV1", REPORT, AlarmSettings())}
    assert found["host:sv1:disk:/"] == (Severity.WARNING, "SV1: / is 90% full")
    assert found["host:sv1:disk:/full"][0] is Severity.CRITICAL and "host:sv1:disk:/data" not in found
    assert found["host:sv1:raid:md127"] == (Severity.CRITICAL, "SV1: RAID md127 is degraded")
    assert found["host:sv1:smart:sdb"][0] is Severity.CRITICAL
    assert found["host:sv1:smart:sda"] == (Severity.WARNING, "SV1: disk sda (TOSHIBA) shows wear")
    assert "host:sv1:disktemp:sda" in found and "host:sv1:disktemp:nvme0n1" not in found
    assert "host:sv1:temp:psu" in found and "host:sv1:temp:cpu" not in found
    assert found["host:sv1:services"] == (Severity.WARNING, "SV1: 1 service has failed")
    assert host_problems("x", "X", {"filesystems": [{"mount": "/"}], "raid": ["junk"]}, AlarmSettings()) == []


def test_a_silent_collector_is_a_warning() -> None:
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    config = Config.model_validate({"devices": {"sv1": {"host": "h", "collect": {}}}})
    st = {
        "sv1": DeviceStatus(checks={"ping": CheckStatus(up=True, since=now - timedelta(days=1), checked=now)})
    }
    st["sv1"].host, st["sv1"].host_checked = REPORT, now - timedelta(minutes=30)
    keys = [p.key for p in evaluate(config, st, now, {}, now - timedelta(hours=1))]
    assert keys == ["host:sv1:collector"]
    st["sv1"].host_checked = now - timedelta(minutes=1)
    assert "host:sv1:raid:md127" in [p.key for p in evaluate(config, st, now, {}, now - timedelta(hours=1))]


def test_image_names_and_versions() -> None:
    assert parse("postgres:16").name == "library/postgres"
    assert parse("lscr.io/linuxserver/plex").tag == "latest"
    assert parse("localhost:5000/me/app:1").registry == "localhost:5000"
    assert parse("x@sha256:abc") is None
    assert newer_tag("1.2.3", ["1.2.4", "1.10.0", "2.0.0-rc1", "v1.11.0"]) == "1.10.0"
    assert newer_tag("16-alpine", ["17-alpine", "17", "18-bookworm"]) == "17-alpine"
    assert newer_tag("latest", ["1.0"]) is None
    # Dates are not versions.
    assert newer_tag("3", ["3", "4", "20260805", "20250108"]) == "4"
    assert newer_tag("20250108", ["20260805"]) == "20260805"
    # Newest within the same major.
    tags = ["16.4", "16.6", "16.10", "17.0", "17.2", "15.9", "16.6-alpine", "20250101"]
    assert newer_tag("16.4", tags) == "17.2"
    assert newer_tag("16.4", tags, same_major=True) == "16.10"
    assert newer_tag("16.10", tags, same_major=True) is None
    assert newer_tag("v2.8.1", ["v2.9.4", "v3.0.0"], same_major=True) == "v2.9.4"
    assert newer_tag("15", ["16", "15"], same_major=True) is None  # one number: no "within"


def fake_registry(digest: str, tags: list[str]):
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.host == "auth.example":
            return httpx.Response(200, json={"token": "t0k", "expires_in": 300})
        if request.headers.get("authorization") != "Bearer t0k":
            return httpx.Response(
                401,
                headers={
                    "www-authenticate": 'Bearer realm="https://auth.example/token",service="reg",scope="repository:me/app:pull"'
                },
            )
        if "/manifests/" in request.url.path:
            return httpx.Response(200, headers={"docker-content-digest": digest})
        return httpx.Response(200, json={"tags": tags})

    return Registries(httpx.MockTransport(handle), allowed_host=lambda host: True), seen


async def test_updates_from_the_registry() -> None:
    regs, seen = fake_registry("sha256:" + "b" * 64, ["1.0.0", "1.1.0"])
    newer = await regs.check("reg.example/me/app:1.0.0", ["reg.example/me/app@sha256:" + "a" * 64])
    assert newer.update and newer.update_to == "1.1.0"
    build = await regs.check("reg.example/me/app:latest", ["reg.example/me/app@sha256:" + "a" * 64])
    assert build.update and build.update_to == "newer build"
    same = await regs.check("reg.example/me/app:latest", ["reg.example/me/app@sha256:" + "b" * 64])
    assert not same.update and same.unchecked is None
    unknown = await regs.check("reg.example/me/app:latest", [])
    assert not unknown.update and "digest" in (unknown.unchecked or "")
    assert seen.count("GET /token") == 1  # the token is kept


async def test_containers_from_the_collector() -> None:
    regs, _ = fake_registry("sha256:" + "b" * 64, [])
    await regs.check("reg.example/me/app:latest", ["reg.example/me/app@sha256:" + "a" * 64])
    items = [
        {
            "name": "norn",
            "image": "reg.example/me/app:latest",
            "image_id": "sha256:" + "c" * 64,
            "digests": ["reg.example/me/app@sha256:" + "a" * 64],
            "state": "running",
        },
        "junk",
    ]
    (c,) = await docker.from_collector(items, regs)
    assert (c.name, c.image, c.tag, c.image_id, c.update_to, c.registry) == (
        "norn",
        "me/app",
        "latest",
        "c" * 12,
        "newer build",
        "reg.example",
    )


def test_collect_is_added_to_the_device(tmp_path: Path) -> None:
    text = "devices:\n  sv1:\n    host: h # the server\n    checks:\n      - ping\n\n# users\nusers: {}\n"
    new = config_edit.set_device_key(text, "sv1", "collect", {})
    assert "    host: h # the server\n    collect: {}\n    checks:\n      - ping\n\n# users\n" in new


async def test_the_registry_client_stays_on_the_internet() -> None:
    import asyncio

    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.host == "reg.example" and "/manifests/" in request.url.path:
            return httpx.Response(302, headers={"location": "https://192.168.15.1/admin?reboot=1"})
        return httpx.Response(200)

    regs = Registries(httpx.MockTransport(handle), allowed_host=lambda host: host == "reg.example")
    r = await regs.check("reg.example/me/app:latest", [])
    assert not r.update and r.unchecked and all("192.168" not in u for u in asked)
    local = await Registries(httpx.MockTransport(handle), allowed_host=lambda h: False).check(
        "10.0.0.1/x/y:1", []
    )
    assert "local network" in (local.unchecked or "")
    # peek never waits: the first answer is "not checked yet", the check runs on its own.
    regs2, _ = fake_registry("sha256:" + "b" * 64, [])
    assert regs2.peek("reg.example/me/app:latest", []).unchecked == "not checked yet"
    await asyncio.sleep(0.05)
    assert regs2.peek("reg.example/me/app:latest", []).unchecked != "not checked yet"


def test_a_hostile_report_is_tamed() -> None:
    nested: list = []
    for _ in range(800):
        nested = [nested]
    bad = {
        "filesystems": 5,
        "smart": [1],
        "services": "x",
        "raid": [{"failed": 5, "name": "md0"}],
        "updates": {"reboot": True, "reboot_for": 5},
        "temperatures": [{"sensor": "x", "celsius": 1e400}],
        "docker": [{"name": "a", "image": "x", "digests": 5}]
        + [{"name": f"c{i}", "image": "i"} for i in range(1000)],
        "junk": nested,
        "system": {"load": "high", "memory": 7, "uptime": 10**1000},
    }
    r = normalize(bad)
    assert "junk" not in r and r["filesystems"] == [] and r["smart"]["disks"] == []
    assert r["services"] == {"failed": []} and r["raid"][0]["failed"] == []
    assert r["temperatures"] == [] and len(r["docker"]) == 300 and r["docker"][0]["digests"] == []
    assert r["updates"]["reboot_for"] == []
    assert r["system"]["uptime"] == 0
    host_problems("x", "X", r, AlarmSettings())  # never raises on what normalize lets through
    json.dumps(r)  # and it serializes


def test_vms_in_the_report() -> None:
    raw = {
        "vms": [
            {"name": "win11", "state": "shut off", "autostart": True, "vcpus": 4, "memory": 8 * 1024**3},
            {"name": "test", "state": "shut off", "autostart": False, "vcpus": "lots", "disks": "no"},
            {"state": "running"},  # no name: dropped
        ]
    }
    vms = normalize(raw)["vms"]
    assert [v["name"] for v in vms] == ["win11", "test"] and vms[1]["vcpus"] is None and vms[1]["disks"] == []
    found = {p.key: p.title for p in host_problems("sv1", "SV1", normalize(raw), AlarmSettings())}
    # One that should start with the host but doesn't: a warning. One left off on purpose: nothing.
    assert found == {"host:sv1:vm:win11": "SV1: VM win11 is shut off"}
    assert normalize({"vms": "junk"})["vms"] is None and "vms" not in normalize({})


def test_the_collector_tells_a_kept_previous_image() -> None:
    """kastellan-previous:<name> (kept by the actor's update) shows as the container's
    `previous` when it isn't what runs."""
    import shutil
    import socketserver
    import tempfile
    import threading

    a, b = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    answers = {
        "/containers/json": [
            {
                "Names": ["/app"],
                "Image": "me/app:1",
                "ImageID": b,
                "State": "running",
                "Ports": [{"PublicPort": 8080, "Type": "tcp"}, {"PublicPort": 5353, "Type": "udp"}],
            },
            {"Names": ["/db"], "Image": "db:2", "ImageID": a, "State": "exited"},
        ],
        "/images/json": [{"Id": a, "RepoTags": ["kastellan-previous:app", "kastellan-previous:db"]}],
    }

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            path = self.rfile.readline().split()[1].decode().split("?")[0]
            while self.rfile.readline() not in (b"\r\n", b""):
                pass
            body = json.dumps(answers.get(path, {"RepoDigests": []})).encode()
            self.wfile.write(b"HTTP/1.0 200 OK\r\n\r\n" + body)

    folder = Path(tempfile.mkdtemp(prefix="kcol", dir="/tmp"))  # short: a socket path's limit
    sock = folder / "d.sock"
    server = socketserver.UnixStreamServer(str(sock), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        code = collector.script().replace("/var/run/docker.sock", str(sock))
        r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
        docker = {c["name"]: c for c in normalize(json.loads(r.stdout))["docker"]}
    finally:
        server.shutdown()
        shutil.rmtree(folder, ignore_errors=True)
    assert docker["app"]["previous"] == "a" * 12
    assert docker["app"]["ports"] == [8080]
    assert docker["db"]["previous"] is None  # it runs that one: nothing to go back to


def test_disk_power_errors_do_not_look_asleep_or_lose_busy_samples() -> None:
    import ast
    from types import SimpleNamespace

    # Exercise the standalone collector function with fake sysfs and smartctl.
    tree = ast.parse(collector.script())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "activity")
    function.decorator_list = []
    codes = {"sleep": 99, "awake": 0, "unsupported": 98, "failed": 2, "timeout": None}

    def run(args, timeout):
        assert args[1:4] == ["-n", "standby,99,98", "-i"]
        code = codes[args[-1].split("/")[-1]]
        if code is None:
            raise subprocess.TimeoutExpired(args, timeout)
        return code, ""

    scope = {
        "disks": lambda: list(codes),
        "read": lambda path: "1" if path.endswith("rotational") else "0 " * 9 + "123",
        "run": run,
        "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _: True)),
        "subprocess": subprocess,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "collector", "exec"), scope)
    rows = scope["activity"]()
    assert [d["asleep"] for d in rows] == [True, False, None, None, None]
    assert all(d["busy_ms"] == 123 for d in rows)


def test_reading_a_host_again(monkeypatch, admin_client) -> None:
    asked = []

    async def fake_collect(keys, device):
        asked.append(device.host)
        return {"version": 1, "errors": {}, "docker": []}

    monkeypatch.setattr(collector, "collect", fake_collect)
    c = admin_client(
        "devices:\n  nas:\n    host: 127.0.0.1\n    collect: {user: kastellan-collect}\n"
        "  cam:\n    host: 127.0.0.2\n"
    )

    def failed_history(*args):
        raise OSError("disk full")

    monkeypatch.setattr(c.app.state.hub.vitals, "add_disks", failed_history)
    r = c.post("/api/devices/nas/collect/now")
    assert r.status_code == 200 and r.json()["checked"] and "127.0.0.1" in asked
    assert c.app.state.hub.monitor.status["nas"].containers == []
    assert c.post("/api/devices/nas/collect/now").status_code == 429  # just read
    assert c.post("/api/devices/cam/collect/now").status_code == 409
