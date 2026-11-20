import asyncio
from pathlib import Path

from kastellan.config import Config
from kastellan.history import History
from kastellan.monitor import Monitor, State


async def test_monitor_follows_config(tmp_path: Path) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monitor = Monitor(History(tmp_path))
    config = Config.model_validate(
        {
            "devices": {
                "box": {
                    "host": "127.0.0.1",
                    "checks": [{"tcp": port, "name": "open"}, {"tcp": 1, "name": "closed"}],
                }
            },
        }
    )
    config.check_interval = 0.05  # below what config.yaml may say, to keep the test fast
    async with server:
        monitor.apply(config)
        for _ in range(100):
            await asyncio.sleep(0.02)
            if monitor.status["box"].state is not State.UNKNOWN and all(
                c.up is not None for c in monitor.status["box"].checks.values()
            ):
                break
        assert monitor.status["box"].state is State.PARTIAL

        monitor.apply(Config.model_validate({"devices": {}}))
        assert monitor.status == {}
    await monitor.stop()


def test_restore_keeps_since(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from kastellan.history import Row

    monitor = Monitor(History(tmp_path))
    config = Config.model_validate({"devices": {"nas": {"host": "nas.lan"}}})
    monitor.prepare(config)
    t = datetime(2026, 1, 1, tzinfo=UTC)
    monitor.restore(
        [
            Row(t, "nas", "ping", True, 1.0, ""),
            Row(t + timedelta(minutes=1), "nas", "ping", False, None, "no reply"),
            Row(t + timedelta(minutes=2), "nas", "ping", False, None, "no reply"),
            Row(t, "gone", "ping", True, 1.0, ""),
        ]
    )
    check = monitor.status["nas"].checks["ping"]
    assert check.up is False and check.since == t + timedelta(minutes=1) and check.since_exact
    assert "gone" not in monitor.status

    monitor.prepare(Config.model_validate({"devices": {"pi": {"host": "pi.lan"}}}))
    monitor.restore([Row(t, "pi", "ping", True, 1.0, "")])
    pi = monitor.status["pi"].checks["ping"]
    assert pi.since == t and not pi.since_exact  # began then or before


async def test_only_changed_checks_restart(tmp_path: Path) -> None:
    monitor = Monitor(History(tmp_path))
    base = {"host": "127.0.0.1", "checks": [{"tcp": 1, "name": "a"}, {"tcp": 2, "name": "b"}]}
    monitor.apply(Config.model_validate({"devices": {"x": base}}))
    tasks = {k: v[0] for k, v in monitor._tasks.items()}

    renamed = {**base, "name": "Renamed", "tags": ["new"]}
    monitor.apply(Config.model_validate({"devices": {"x": renamed}}))
    assert {k: v[0] for k, v in monitor._tasks.items()} == tasks  # nothing restarted

    changed = {**base, "checks": [{"tcp": 1, "name": "a"}, {"tcp": 3, "name": "b"}]}
    monitor.apply(Config.model_validate({"devices": {"x": changed}}))
    assert monitor._tasks[("x", "a")][0] is tasks[("x", "a")]
    assert monitor._tasks[("x", "b")][0] is not tasks[("x", "b")]

    monitor.status["x"].checks["a"].up = True
    moved = {**changed, "host": "127.0.0.2"}
    monitor.apply(Config.model_validate({"devices": {"x": moved}}))
    assert monitor.status["x"].checks["a"].up is None  # a new address starts unknown
    await monitor.stop()


async def test_software_changes_are_logged(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from kastellan.checks import Result
    from kastellan.software import Software
    from kastellan.versions import Versions

    versions = Versions(tmp_path / "versions.log")
    monitor = Monitor(History(tmp_path), versions=versions)
    config = Config.model_validate({"devices": {"sv1": {"host": "x", "checks": [{"tcp": 22}]}}})
    monitor.prepare(config)
    ssh = monitor.status["sv1"].checks["tcp:22"]

    def see(software: Software | None) -> None:
        monitor._record("sv1", "tcp:22", Result(True, 1.0, software=software))

    see(Software("OpenSSH", "9.8"))
    assert ssh.software is None  # once is not enough
    see(Software("OpenSSH", "9.8"))
    assert ssh.software == "OpenSSH 9.8"
    see(None)  # said nothing this time: keep what we know
    see(Software("Caddy"))  # a one-off odd answer: not an update
    see(Software("OpenSSH", "9.8"))
    assert ssh.software == "OpenSSH 9.8"
    see(Software("OpenSSH", "9.9"))
    see(Software("OpenSSH", "9.9"))
    assert ssh.software == "OpenSSH 9.9"
    [change] = versions.changes(datetime(2000, 1, 1, tzinfo=UTC))
    assert (change["from"], change["to"]) == ("OpenSSH 9.8", "OpenSSH 9.9")

    # After a restart the hub knows the software before the first check runs...
    fresh = Monitor(History(tmp_path), versions=versions)
    fresh.prepare(config)
    fresh.restore_software(versions.latest(), config)
    assert fresh.status["sv1"].checks["tcp:22"].software == "OpenSSH 9.9"
    # ...but not when the check now looks at another host.
    moved = Config.model_validate({"devices": {"sv1": {"host": "y", "checks": [{"tcp": 22}]}}})
    other = Monitor(History(tmp_path), versions=versions)
    other.prepare(moved)
    other.restore_software(versions.latest(), moved)
    assert other.status["sv1"].checks["tcp:22"].software is None


def test_version_log_compaction_and_bad_lines(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from kastellan.versions import Versions

    versions = Versions(tmp_path / "versions.log")
    now = datetime(2026, 6, 1, tzinfo=UTC)
    versions.record(now - timedelta(days=500), "a", "ssh", None, "v1")
    versions.record(now - timedelta(days=400), "a", "ssh", "v1", "v2")
    versions.record(now - timedelta(days=400), "b", "web", None, "x")
    versions.record(now - timedelta(days=1), "a", "ssh", "v2", "v3")
    with versions.path.open("a") as f:
        f.write('{"time": "2026-05-01T00:00:00", "device": "a"}\n{"broken\n')  # naive, partial
    versions.compact(365, now=now)
    kept = [(e["device"], e["to"]) for e in versions._entries()]
    assert kept == [("b", "x"), ("a", "v3")]  # b's only entry stays: it is what b runs
    assert versions.changes(now - timedelta(days=30))[0]["to"] == "v3"
