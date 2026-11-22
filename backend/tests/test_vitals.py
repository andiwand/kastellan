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
