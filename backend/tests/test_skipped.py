"""A device's own update offers said to be not needed: left out until they change."""

from pathlib import Path


def test_firmware_reboot_and_packages_not_needed(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.monitor import DeviceStatus
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text("devices:\n  ap:\n    host: 127.0.0.1\n  sv1:\n    host: 127.0.0.2\n")
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        hub = c.app.state.hub
        hub.monitor.status["ap"] = DeviceStatus(router={"firmware": {"next_step": "23.05.6"}})
        host = {"updates": {"available": 3, "security": 1, "reboot": True, "reboot_for": ["linux-image-7.0"]}}
        hub.monitor.status["sv1"] = DeviceStatus(host=host)

        def skipped(id: str) -> list[str]:
            return next(d for d in c.get("/api/devices").json() if d["id"] == id)["updates_skipped"]

        assert skipped("ap") == [] and skipped("sv1") == []
        assert c.post("/api/devices/ap/updates/firmware/not-needed", headers=h).json() == {
            "skipped": "OpenWrt 23.05.6"
        }
        assert c.post("/api/devices/sv1/updates/reboot/not-needed", headers=h).status_code == 200
        assert c.post("/api/devices/sv1/updates/packages/not-needed", headers=h).status_code == 200
        assert skipped("ap") == ["firmware"] and sorted(skipped("sv1")) == ["packages", "reboot"]
        # Changed offers show again: a newer release, a reboot for another kernel, another update.
        hub.monitor.status["ap"].router["firmware"]["next_step"] = "24.10.8"
        host["updates"]["reboot_for"] = ["linux-image-7.1"]
        host["updates"]["available"] = 4
        assert skipped("ap") == [] and skipped("sv1") == []
        assert c.post("/api/devices/ap/updates/reboot/not-needed", headers=h).status_code == 404
        assert c.post("/api/devices/ap/updates/nonsense/not-needed", headers=h).status_code == 404
