"""Vitals: one shape from hosts, OpenWrt and FRITZ!Boxes; CPU from counters; kept for charts."""

from pathlib import Path

from kastellan import vitals
from kastellan.openwrt import setup
from kastellan.vitals import Cpu, Store, Vitals


def test_cpu_percent_from_two_readings() -> None:
    cpu = Cpu()
    assert cpu.percent("h", [100, 0, 100, 800, 0, 0, 0, 0]) is None  # the first: nothing to compare
    assert cpu.percent("h", [150, 0, 150, 900, 0, 0, 0, 0]) == 50.0  # 100 busy of 200
    assert cpu.percent("h", [150, 0, 150, 1000, 100, 0, 0, 0]) == 0.0  # iowait counts as idle
    assert cpu.percent("h", [1, 0, 1, 10, 0, 0, 0, 0]) is None  # restarted: counters began anew
    assert cpu.percent("h", "nonsense") is None and cpu.percent("h", [1, 2]) is None


def test_a_hosts_vitals() -> None:
    cpu = Cpu()
    report = {
        "system": {
            "load": [0.5, 0.4, 0.3],
            "cpus": 4,
            "cpu": [0, 0, 0, 0],
            "memory": {"total": 1000, "available": 250},
        },
        "filesystems": [
            {"mount": "/", "used": 30, "available": 70},
            {"mount": "/data", "used": 90, "available": 10},
            {"mount": "/odd", "used": "x", "available": 1},
        ],
        "temperatures": [{"celsius": 41.0}, {"celsius": 63.5}, {"celsius": 999}],
    }
    v = vitals.from_host("h", report, cpu)
    assert (v.memory, v.storage, v.storage_mount, v.temperature) == (75.0, 90.0, "/data", 63.5)
    assert (v.load, v.cores, v.cpu) == (0.5, 4, None)
    assert vitals.from_host("h", {}, cpu).empty()


def test_an_openwrt_devices_vitals() -> None:
    cpu = Cpu()
    router = {
        "system": {"load": [0.1], "memory": {"total": 1000, "free": 100, "buffered": 50, "available": 400}},
        "storage": {"total": 8000, "used": 2000, "avail": 6000},
    }
    v = vitals.from_openwrt("r", router, {"cpu": [10, 0, 10, 80, 0, 0, 0, 0], "temp": 51234}, cpu)
    assert (v.memory, v.storage, v.temperature, v.cpu) == (60.0, 25.0, 51.2, None)
    v = vitals.from_openwrt("r", router, {"cpu": [20, 0, 20, 160, 0, 0, 0, 0], "temp": 0}, cpu)
    assert v.cpu == 20.0 and v.temperature is None
    old = {"system": {"memory": {"total": 1000, "free": 100, "buffered": 100}}}  # before 21.02
    assert vitals.from_openwrt("o", old, None, cpu).memory == 80.0


def test_a_fritzboxs_energy_monitor() -> None:
    eco = {
        "cpuutil": {"series": [[10, 12, 17]]},
        "cputemp": {"series": [[60, 61, 62]]},
        "ramusage": {"series": [[30, 30], [20, 25], [50, 45]]},
    }
    v = vitals.from_fritz(eco)
    assert (v.cpu, v.temperature, v.memory) == (17.0, 62.0, 55.0)
    assert vitals.from_fritz({"cpuutil": "?"}).empty() and vitals.from_fritz(None).empty()


def test_kept_at_most_every_while_and_bucketed(tmp_path: Path) -> None:
    store = Store(tmp_path / "vitals.db")
    t0 = 1_000_000.0
    assert store.add("a", Vitals(cpu=10, memory_used=1, memory_total=4), now=t0)
    assert not store.add("a", Vitals(cpu=90), now=t0 + 10)  # too soon
    assert store.add("a", Vitals(cpu=30), now=t0 + 200)
    assert not store.add("b", Vitals(), now=t0)  # nothing known: nothing kept
    data = store.series(["a", "b"], t0, 600, 3)
    assert data == {
        "a": {
            "cpu": [20.0, None, None],
            "memory": [25.0, None, None],
            "storage": [None] * 3,
            "temperature": [None] * 3,
        }
    }
    assert store.series([], t0, 600, 3) == {}


def test_the_read_group_runs_the_vitals_helper() -> None:
    files = setup.acl()["kastellan-read"]["read"]["file"]
    assert files[setup.VITALS] == ["exec"]
    assert "vitals" in setup.helper_digests()
    assert setup.VITALS in setup.install_script(set(), setup.md5crypt("pw"))


def test_the_endpoint(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text("devices:\n  nas:\n    host: 127.0.0.1\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        hub = c.app.state.hub
        import time

        hub.vitals.add("nas", Vitals(cpu=42), now=time.time() - 60)
        r = c.get("/api/vitals", params={"device": "nas", "hours": 1, "buckets": 4}).json()
        assert r["devices"]["nas"]["cpu"][-1] == 42.0 and len(r["devices"]["nas"]["cpu"]) == 4
        assert c.get("/api/vitals", params={"device": "nope"}).status_code == 404
        assert "nas" in c.get("/api/vitals").json()["devices"]


def test_cpu_and_compose_survive_the_collector_boundary() -> None:
    from kastellan.host.report import normalize

    cpu = Cpu()
    first = normalize(
        {
            "system": {"cpu": [100, 0, 100, 800]},
            "docker": [{"name": "app", "image": "repo/app:1", "compose": "/srv/app/compose.yaml"}],
        }
    )
    assert vitals.from_host("h", first, cpu).cpu is None
    second = normalize({"system": {"cpu": [150, 0, 150, 900]}})
    assert vitals.from_host("h", second, cpu).cpu == 50
    assert first["docker"][0]["compose"] == "/srv/app/compose.yaml"
    counters = [2**53 + 1, 0, 100, 800]
    assert normalize({"system": {"cpu": counters}})["system"]["cpu"] == counters
    for bad in ([1, 2], [1, 2, True, 4], [1, 2, -3, 4], [1, 2, float("inf"), 4], [1, 2, 2**64, 4]):
        assert normalize({"system": {"cpu": bad}})["system"]["cpu"] is None
        assert cpu.percent("bad", bad) is None


def test_missing_samples_are_not_replaced_by_older_values() -> None:
    assert vitals.from_fritz({"cpuutil": {"series": [[10, None]]}}).empty()
    assert vitals.from_fritz({"cputemp": {"series": [[50, float("inf")]]}}).empty()
    assert vitals.from_fritz({"cpuutil": {"series": [[10**1000]]}}).empty()
    v = vitals.from_openwrt("r", {"system": {"memory": {"total": 1000, "free": 0}}}, {"temp": 500}, Cpu())
    assert v.memory == 100 and v.temperature == 0.5
    assert Vitals(memory_used=float("nan"), memory_total=10).memory is None


def test_concurrent_samples_and_shutdown(tmp_path: Path) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor

    store = Store(tmp_path / "vitals.db")
    barrier = threading.Barrier(8)

    def add(_):
        barrier.wait()
        return store.add("h", Vitals(cpu=50), now=1_000_000)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(add, range(8))) == 1
    store.close()
    assert not store.add("h", Vitals(cpu=50), now=1_000_200)
    assert store._conn is None


async def test_empty_readings_and_replaced_devices_clear_current_vitals(tmp_path: Path) -> None:
    from kastellan.hub import Hub
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text("devices: {nas: {host: 127.0.0.1}}")
    hub = Hub(Settings(config=config, data=tmp_path))
    try:
        hub.reload()
        status = hub.monitor.status["nas"]
        await hub.note_vitals("nas", status, Vitals(cpu=50))
        await hub.note_vitals("nas", status, Vitals())
        assert status.vitals is None
        await hub.note_vitals("nas", status, Vitals(cpu=50))
        assert hub._cpu.percent("nas", [100, 0, 100, 800]) is None
        config.write_text("devices: {nas: {host: 127.0.0.2}}")
        hub.reload()
        assert hub.monitor.status["nas"].vitals is None
        assert hub._cpu.percent("nas", [150, 0, 150, 900]) is None
    finally:
        await hub.stop()


def test_disks_busy_asleep_and_warm(tmp_path: Path) -> None:
    from kastellan.host.report import normalize
    from kastellan.vitals import Busy, DiskSample, disks_of

    busy = Busy()
    assert busy.percent("h", "sda", 1000, 100.0) is None  # the first reading
    assert busy.percent("h", "sda", 31_000, 160.0) == 50.0  # 30 s busy in 60 s
    assert busy.percent("h", "sda", 5, 220.0) is None  # the counter began anew

    report = normalize(
        {
            "activity": [
                {"disk": "sda", "busy_ms": 0, "rotational": True, "asleep": True},
                {"disk": "nvme0n1", "busy_ms": 0, "rotational": False, "asleep": "?"},
                {"disk": "", "busy_ms": 1},
            ],
            "smart": {"available": True, "disks": [{"disk": "nvme0n1", "temperature": 41}]},
        }
    )
    assert [d["disk"] for d in report["activity"]] == ["sda", "nvme0n1"]
    samples = disks_of("h", report, Busy(), 100.0)
    assert samples == [DiskSample("sda", None, True, None), DiskSample("nvme0n1", None, None, 41.0)]

    store = Store(tmp_path / "vitals.db")
    t0 = 1_000_000.0
    assert store.add_disks("h", [DiskSample("sda", 0.0, True, None)], now=t0)
    assert not store.add_disks("h", [DiskSample("sda", 9.0, False, 35.0)], now=t0 + 10)  # too soon
    assert store.add_disks("h", [DiskSample("sda", 20.0, False, 35.0)], now=t0 + 200)
    assert store.disk_series("h", t0, 600, 2) == {
        "sda": {"busy": [10.0, None], "asleep": [50.0, None], "temperature": [35.0, None]}
    }


def test_the_disks_route(admin_client) -> None:
    import time

    from kastellan.vitals import DiskSample

    c = admin_client("devices:\n  nas:\n    host: 192.0.2.2\n")
    c.app.state.hub.vitals.add_disks("nas", [DiskSample("sda", 12.0, False, 38.0)], now=time.time() - 60)
    r = c.get("/api/devices/nas/disks", params={"hours": 1, "buckets": 4}).json()
    assert r["disks"]["sda"]["busy"][-1] == 12.0
    assert c.get("/api/devices/nope/disks").status_code == 404
