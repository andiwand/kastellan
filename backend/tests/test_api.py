from pathlib import Path

from fastapi.testclient import TestClient

from kastellan.api import create_app
from kastellan.settings import Settings


def test_devices_and_health(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("devices:\n  router:\n    host: 127.0.0.1\n    kind: router\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as client:
        assert client.get("/api/health").json()["config_error"] is None
        [router] = client.get("/api/devices").json()
        assert router["id"] == "router" and router["kind"] == "router"
        assert [c["name"] for c in router["checks"]] == ["ping"]
        assert client.get("/api/devices/nope/history").status_code == 404
        assert client.get("/api/nope").status_code == 404
        assert client.get("/api/series", params={"hours": "nan"}).status_code == 422  # not a 500
        for route in ("/api/series", "/api/changes", "/api/activity"):
            assert client.get(route, params={"hours": "-1e300"}).status_code == 422


def test_demo(tmp_path: Path) -> None:
    settings = Settings(config=tmp_path / "none.yaml", data=tmp_path, demo=True)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["demo"]
        devices = {d["id"]: d for d in client.get("/api/devices").json()}
        assert devices["gaming-pc"]["always_on"] is False
        series = client.get("/api/series", params={"hours": 24, "buckets": 24}).json()
        assert len(series["devices"]["nas"]["jellyfin"]) == 24
        changes = client.get("/api/changes").json()
        assert any(c["device"] == "printer" and c["kind"] == "state" for c in changes)
        [update] = [c for c in changes if c["kind"] == "version"]
        assert (update["device"], update["to"]) == ("homeserver", "OpenSSH 10.2p1 (Debian)")
        ssh = next(c for c in devices["homeserver"]["checks"] if c["name"] == "ssh")
        assert ssh["software"] == "OpenSSH 10.2p1 (Debian)"
    assert not (tmp_path / "checks").exists()  # demo history stays out of the real data


def test_demo_containers_have_unique_keys(tmp_path: Path) -> None:
    """The web app keys lists by these; a missing key crashed the overview."""
    from kastellan.demo import CONTAINERS
    from kastellan.docker import Container

    fields = set(Container.__dataclass_fields__)
    for containers in CONTAINERS.values():
        assert all(set(c) == fields for c in containers)
        assert len({c["key"] for c in containers}) == len(containers)


def test_demo_logs_backups_internet_wifi(tmp_path: Path) -> None:
    settings = Settings(config=tmp_path / "none.yaml", data=tmp_path, demo=True)
    with TestClient(create_app(settings)) as client:
        lines = client.get("/api/logs", params={"device": "repeater-living", "level": 4}).json()["lines"]
        assert lines and all(ln["device"] == "repeater-living" and ln["level"] <= 4 for ln in lines)
        assert client.get("/api/logs/facets").json()["total"] > 10
        overview = {b["device"]: b for b in client.get("/api/device-backups").json()}
        assert overview["repeater-living"]["versions"] == 3 and overview["fritzbox"]["versions"] == 2
        versions = client.get("/api/devices/repeater-living/backups").json()["versions"]
        newer, older = versions[0]["id"], versions[1]["id"]
        # Comparing and downloading need a signed-in admin (sign-in is off in this demo).
        assert (
            client.get(
                "/api/devices/repeater-living/backups/diff", params={"older": older, "newer": newer}
            ).status_code
            == 403
        )
        net = client.get("/api/internet", params={"hours": 168}).json()
        assert net["router"] == "fritzbox" and len(net["series"]) == 84
        assert any(o["step"] == "internet" for o in net["outages"]) and net["speedtests"]
        w = client.get("/api/wifi").json()
        castle = next(n for n in w["networks"] if n["ssid"] == "Castle")
        assert set(castle["devices"]) == {"fritzbox", "repeater-attic", "repeater-living"}
        plan = client.get("/api/wifi/plan").json()
        ours = {(r["device"], r["band"]): r["channel"] for r in plan["radios"]}
        assert len({ours[("repeater-living", "2g")], ours[("repeater-attic", "2g")]}) == 2
    assert not (tmp_path / "logs.db").exists() and not (tmp_path / "device-backups").exists()
