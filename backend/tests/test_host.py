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
    # The script it installs is itself valid Python.
    compile(collector.script(), "kastellan-collect", "exec")


def test_the_collector_runs_and_never_fails_whole() -> None:
    """On any machine (here maybe without /proc): JSON, with what it couldn't read in errors."""
    r = subprocess.run([sys.executable, "-I", "-c", collector.script()], capture_output=True, text=True)
    data = json.loads(r.stdout)
    assert data["version"] == 1 and isinstance(data["errors"], dict)


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


def test_dates_are_not_versions() -> None:
    assert newer_tag("3", ["3", "4", "20260805", "20250108"]) == "4"
    assert newer_tag("20250108", ["20260805"]) == "20260805"


def test_a_hostile_report_is_tamed() -> None:
    from kastellan.host.report import normalize

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
        "system": {"load": "high", "memory": 7},
    }
    r = normalize(bad)
    assert "junk" not in r and r["filesystems"] == [] and r["smart"]["disks"] == []
    assert r["services"] == {"failed": []} and r["raid"][0]["failed"] == []
    assert r["temperatures"] == [] and len(r["docker"]) == 300 and r["docker"][0]["digests"] == []
    assert r["updates"]["reboot_for"] == []
    host_problems("x", "X", r, AlarmSettings())  # never raises on what normalize lets through
    import json as _json

    _json.dumps(r)  # and it serializes
