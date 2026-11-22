"""Shared SSIDs must produce distinct rows on the Wi-Fi page."""

from kastellan import roaming, wifi


def test_each_network_keeps_its_own_roaming_finding(demo_client) -> None:
    hub = demo_client.app.state.hub
    members = ["repeater-attic", "repeater-garage"]
    survey = {
        "networks": [
            {"ssid": ssid, "devices": members, "on": [{"device": d} for d in members]}
            for ssid in ("Home", "Guests")
        ]
    }
    findings = wifi.findings(
        networks=[],
        radios=[],
        clients=[],
        errors={},
        country=None,
        meshes=[],
        fritz_mesh={},
        roaming={"aps": roaming.check(hub, members, survey)},
        scanned=True,
        names={},
    )
    ft = [f for f in findings if f["key"].startswith("roaming:aps:ft:")]
    assert len(ft) == 2 and len({f["key"] for f in ft}) == 2
