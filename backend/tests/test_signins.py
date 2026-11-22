from kastellan import signins


def line(msg: str, program: str = "sshd", ts: float = 1000.0, device: str = "gw", id: int = 1) -> dict:
    return {"id": id, "ts": ts, "device": device, "program": program, "msg": msg, "verified": True}


def test_sign_in_lines_are_read() -> None:
    cases = [
        (
            "Accepted publickey for root from 192.168.1.10 port 41750 ssh2",
            "ssh",
            "ok",
            "root",
            "192.168.1.10",
        ),
        (
            "Failed password for invalid user admin from 203.0.113.5 port 22 ssh2",
            "ssh",
            "failed",
            "admin",
            "203.0.113.5",
        ),
        ("Invalid user pi from 171.237.170.148 port 51234", "ssh", "failed", "pi", "171.237.170.148"),
        (
            "Connection closed by authenticating user root 92.118.39.71 port 40112 [preauth]",
            "ssh",
            "attempt",
            "root",
            "92.118.39.71",
        ),
        (
            "Disconnected from invalid user sysadmin 62.60.130.219 port 1 [preauth]",
            "ssh",
            "attempt",
            "sysadmin",
            None,
        ),
        ("Connection closed by 193.24.211.206 port 5555 [preauth]", "ssh", "attempt", None, "193.24.211.206"),
        ("Password auth succeeded for 'root' from 192.168.1.10:41750", "ssh", "ok", "root", "192.168.1.10"),
        ("Bad password attempt for 'root' from 203.0.113.9:4000", "ssh", "failed", "root", "203.0.113.9"),
        (
            "Exit before auth from <192.168.1.10:5000>: Exited normally",
            "ssh",
            "attempt",
            None,
            "192.168.1.10",
        ),
        (
            "tcp4-server:198.51.100.7:51544 [alice] Peer Connection Initiated with [AF_INET]198.51.100.7:1",
            "vpn",
            "ok",
            "alice",
            "198.51.100.7",
        ),
        ("phone/198.51.100.7:51544 Peer Connection Initiated", "vpn", "ok", "phone", "198.51.100.7"),
        (
            "tcp4-server:66.132.186.185:40000 TLS Error: cannot locate HMAC in incoming packet",
            "vpn",
            "failed",
            None,
            "66.132.186.185",
        ),
        (
            "TCP connection established with [AF_INET]66.132.195.210:2076",
            "vpn",
            "attempt",
            None,
            "66.132.195.210",
        ),
        (
            "Anmeldung der Benutzerin/des Benutzers admin an der FRITZ!Box-Benutzeroberfläche von "
            "IP-Adresse 192.168.178.20.",
            "router",
            "ok",
            "admin",
            "192.168.178.20",
        ),
        (
            "Anmeldung der Benutzerin admin an der FRITZ!Box-Benutzeroberfläche fehlgeschlagen "
            "(IP-Adresse: 192.168.178.66).",
            "router",
            "failed",
            None,
            "192.168.178.66",
        ),
    ]
    for msg, service, outcome, user, address in cases:
        e = signins.parse(line(msg))
        assert e is not None, msg
        assert (e.service, e.outcome, e.user) == (service, outcome, user), msg
        if address:
            assert e.address == address, msg
    assert signins.parse(line("hostapd: STA aa:bb authenticated")) is None
    assert signins.parse(line("Accepted publickey for root from 203.0.113.5 port 1 ssh2")).outside
    wg = signins.parse(line("alice connected (handshake; 10.8.0.2/32)", program="wireguard(home)"))
    assert wg is not None and wg.via == "10.8.0.2" and wg.user == "alice"


def test_sources_are_flagged() -> None:
    events = [signins.parse(line(f"Invalid user u{i} from 203.0.113.5 port {i}", ts=i)) for i in range(12)]
    events.append(signins.parse(line("Accepted publickey for bob from 198.51.100.1 port 1 ssh2", ts=50)))
    events.append(signins.parse(line("Accepted publickey for bob from 198.51.100.2 port 1 ssh2", ts=60)))
    events.append(signins.parse(line("Accepted password for root from 10.8.0.2 port 1 ssh2", ts=70)))
    events += signins.from_audit(
        [
            {
                "time": "1970-01-01T00:01:40+00:00",
                "action": "signin.failed",
                "user": "x",
                "address": "192.168.1.5",
            },
            {"time": "1970-01-01T00:01:40+00:00", "action": "vault.unlock", "user": "x"},
        ],
        0,
    )
    signins.mark_paths(events, {"10.8.0.2": "alice (home)"})
    by = {s.address: s for s in signins.sources(events, known_ok={"198.51.100.2"})}
    assert by["203.0.113.5"].flags == ["repeated"] and by["203.0.113.5"].failed == 12
    assert by["198.51.100.1"].flags == ["outside-ok", "new-outside"]
    assert by["198.51.100.2"].flags == ["outside-ok"]
    assert by["10.8.0.2"].via == "alice (home)" and not by["10.8.0.2"].outside
    assert by["192.168.1.5"].services == {"hub"}
    summary = signins.summary(events)
    assert summary["by_service"]["ssh"]["failed"] == 12 and summary["outside_ok"] == 2


def test_the_access_page_vpn_servers_and_found_devices_in_demo(demo_client) -> None:
    client = demo_client
    body = client.get("/api/signins", params={"hours": 24}).json()
    services = {e["service"] for e in body["events"]}
    assert {"ssh", "vpn", "router"} <= services
    flags = {s["address"]: s["flags"] for s in body["sources"]}
    assert "outside-ok" not in flags["198.51.100.7"]  # the demo NAS sends unverified syslog
    assert any(e["address"] == "198.51.100.7" and not e["verified"] for e in body["events"])
    assert client.get("/api/signins", params={"hours": 0}).status_code == 422
    # The overview's summary: the same numbers, no addresses or events.
    brief = client.get("/api/signins/summary", params={"hours": 24}).json()
    assert brief["summary"] == body["summary"] and brief["total"] == body["total"]
    assert set(brief) == {"hours", "summary", "total", "flags"}
    flagged = [f for s in body["sources"] for f in s["flags"]]
    assert sum(brief["flags"].values()) == len(flagged)
    servers = client.get("/api/vpn").json()["servers"]
    assert {s["name"] for s in servers} >= {"home_udp", "wg-easy"}

    # Devices found on the network: the demo FRITZ!Box has clients Kastellan doesn't know.
    found = client.get("/api/discovered").json()
    assert found
    h = {"X-Kastellan": "1"}
    mac = found[0]["mac"]
    assert client.post(f"/api/discovered/{mac}/ignore", headers=h).json() == {"ok": True}
    assert mac not in {f["mac"] for f in client.get("/api/discovered").json()}
    assert client.post("/api/discovered/nope/ignore", headers=h).status_code == 400
    assert not (
        client.app.state.hub.settings.data / "discovery-ignored.json"
    ).exists()  # the demo writes nothing


def test_unverified_syslog_cannot_report_a_confirmed_outside_login() -> None:
    e = signins.parse(line("Accepted password for root from 198.51.100.1 port 22 ssh2") | {"verified": False})
    assert e is not None and not e.verified
    assert signins.sources([e], set())[0].flags == []


def test_signins_are_private_while_signin_is_off(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.settings import Settings

    path = tmp_path / "config.yaml"
    path.write_text("devices: {}\n")
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as client:
        assert client.get("/api/signins").status_code == 403
        assert client.get("/api/signins/summary").status_code == 403
