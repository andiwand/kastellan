"""UPS devices: the collector reading apcupsd and NUT, the report's bounds, the config (a UPS with
no address, powered_by), the API, and the alarms (on battery, its limits, folding)."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kastellan import ups
from kastellan.alarms import Severity, evaluate
from kastellan.config import Config
from kastellan.host import collector
from kastellan.host.report import normalize
from kastellan.monitor import CheckStatus, DeviceStatus

# As apcaccess prints it (apcupsd 3.14): `KEY : value`, units after the numbers, times with zones.
APCACCESS = """APC      : 001,036,0868
DATE     : 2026-11-21 10:00:00 +0100
HOSTNAME : sv1
UPSNAME  : sv1-ups
MODEL    : Back-UPS XS 700U
STATUS   : ONBATT LOWBATT
LINEV    : 0.0 Volts
LOADPCT  : 12.0 Percent
BCHARGE  : 18.0 Percent
TIMELEFT : 4.5 Minutes
BATTV    : 11.9 Volts
LASTXFER : Unacceptable line voltage changes
NUMXFERS : 2
XONBATT  : 2026-11-21 09:58:00 +0100
XOFFBATT : 2026-11-20 22:14:28 +0100
SERIALNO : 3B1234X56789
BATTDATE : 2023-04-12
NOMPOWER : 390 Watts
FIRMWARE : 924.Z3 .I USB FW:Z3
END APC  : 2026-11-21 10:00:01 +0100
"""
# As upsc prints it (NUT): `key: value`; runtime in seconds; no transfer count.
UPSC = """battery.charge: 87
battery.runtime: 1260
battery.voltage: 13.2
device.model: CP1500EPFCLCD
device.serial: CTHGQ2000123
input.voltage: 229.0
input.transfer.reason: input voltage out of range
ups.load: 31
ups.realpower.nominal: 900
ups.status: OL CHRG RB
"""


def fake(tmp_path: Path, name: str, body: str) -> Path:
    tool = tmp_path / name
    tool.write_text(f"#!{sys.executable}\nimport sys\n{body}\n")
    tool.chmod(0o755)
    return tool


def collect(tmp_path: Path, apc: str | None, upsc: str | None, budget: float = 15) -> dict:
    code = collector.script().replace("UPS_WITHIN = 15", f"UPS_WITHIN = {budget}")
    tools = {
        'APCACCESS = ("/usr/sbin/apcaccess", "/sbin/apcaccess", "/usr/bin/apcaccess")': ("APCACCESS", apc),
        'UPSC = ("/usr/bin/upsc", "/bin/upsc", "/usr/sbin/upsc")': ("UPSC", upsc),
    }
    for line, (var, body) in tools.items():
        path = fake(tmp_path, var.lower(), body) if body is not None else tmp_path / "none"
        code = code.replace(line, f"{var} = ({str(path)!r},)")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True).stdout
    return json.loads(out)


def test_apcupsd_and_nut_are_read(tmp_path: Path) -> None:
    data = collect(
        tmp_path,
        f"print({APCACCESS!r})",
        "print('cyber') if sys.argv[1:] == ['-l'] else print(" + repr(UPSC) + ")",
    )
    apc, nut = data["ups"]
    assert apc["flags"] == ["on_battery", "low_battery"] and apc["charge"] == 18.0
    assert apc["runtime"] == 270  # 4.5 minutes
    assert apc["transfers"] == 2 and apc["last_transfer"] == "Unacceptable line voltage changes"
    assert apc["model"] == "Back-UPS XS 700U" and apc["nominal_power"] == 390
    assert apc["on_battery_since"] == "2026-11-21 09:58:00 +0100"
    assert nut["name"] == "cyber" and nut["flags"] == ["online", "replace_battery"]
    assert nut["runtime"] == 1260 and nut["last_transfer"] == "input voltage out of range"


def test_no_tools_no_section_and_a_daemon_out_of_reach(tmp_path: Path) -> None:
    assert collect(tmp_path, None, None).get("ups") is None
    gone = collect(tmp_path, "print('Error contacting host localhost port 3551'); sys.exit(1)", None)
    assert gone["ups"] == [{"source": "apcupsd", "status": "", "flags": ["no_contact"]}]


def test_the_report_keeps_ups_data_in_bounds() -> None:
    r = normalize({"ups": [{"flags": ["online", "evil"], "charge": 400, "runtime": -1, "name": "x" * 500}]})
    u = r["ups"][0]
    assert u["flags"] == ["online"] and u["charge"] is None and u["runtime"] is None
    assert len(u["name"]) == 64


def test_a_hung_ups_does_not_discard_earlier_readings(tmp_path: Path) -> None:
    data = collect(
        tmp_path,
        f"print({APCACCESS!r})",
        "import time\nprint('cyber') if sys.argv[1:] == ['-l'] else time.sleep(10)",
        budget=0.3,
    )
    assert data["ups"][0]["charge"] == 18
    assert data["ups"][1]["flags"] == ["no_contact"]


def config(**extra: dict) -> Config:
    return Config.model_validate(
        {
            "devices": {
                "sv1": {"host": "s", "kind": "server", "powered_by": "ups1"},
                "nas": {"host": "n", "powered_by": "ups1"},
                "ups1": {"kind": "ups", "name": "UPS", "ups": {"via": "sv1"}},
                **extra,
            }
        }
    )


def test_a_ups_has_no_address_of_its_own() -> None:
    c = config()
    assert c.devices["ups1"].host == "" and c.devices["ups1"].checks == []
    for bad in (
        {"x": {"kind": "server"}},  # no host and no ups: needs one
        {"x": {"kind": "ups", "ups": {"via": "x"}}},  # itself
        {"x": {"kind": "ups", "ups": {"via": "nope"}}},
        {"x": {"kind": "ups", "ups": {"via": "ups1"}}},  # the reader needs an address
        {"x": {"host": "h", "powered_by": "nas"}},  # not a UPS
        {"x": {"kind": "ups", "ups": {"via": "sv1"}, "checks": ["ping"]}},  # checks need a host
        {"x": {"host": "h", "ups": {"via": "sv1"}}},  # ups: only for kind ups
    ):
        with pytest.raises(ValueError):
            config(**bad)
    with pytest.raises(ValueError):  # a circle of UPSes
        config(
            a={"kind": "ups", "ups": {"via": "sv1"}, "powered_by": "b"},
            b={"kind": "ups", "ups": {"via": "sv1"}, "powered_by": "a"},
        )


NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
STARTED = NOW - timedelta(hours=1)


def status(c: Config, reading: dict, down: tuple[str, ...] = ()) -> dict[str, DeviceStatus]:
    out = {
        id: DeviceStatus(
            checks={
                ch.name: CheckStatus(up=id not in down, since=NOW - timedelta(minutes=10), checked=NOW)
                for ch in d.checks
            }
        )
        for id, d in c.devices.items()
    }
    out["sv1"].host, out["sv1"].host_checked = {"ups": [reading]}, NOW
    return out


def test_on_battery_warns_then_is_critical_and_folds_what_it_powers() -> None:
    c = config()
    battery = {"flags": ["on_battery"], "charge": 80.0, "runtime": 1800.0, "on_battery_since": None}
    found = evaluate(c, status(c, battery), NOW, {}, STARTED)
    assert [(p.key, p.severity) for p in found] == [("ups:ups1:battery", Severity.WARNING)]
    assert found[0].detail == "80 %, 30 min left; on battery: nas, sv1"
    low = {**battery, "charge": 25.0}  # below the 30 % limit
    found = evaluate(c, status(c, low, down=("nas",)), NOW, {}, STARTED)
    assert [(p.key, p.severity) for p in found] == [("ups:ups1:battery", Severity.CRITICAL)]
    assert found[0].hidden == ["nas"]  # down while its UPS is on battery: in the UPS's alarm


def test_on_mains_devices_alarm_on_their_own() -> None:
    c = config()
    online = {"flags": ["online"], "charge": 100.0, "runtime": 3000.0}
    found = evaluate(c, status(c, online, down=("nas",)), NOW, {}, STARTED)
    assert [p.key for p in found] == ["down:nas"]


def test_no_contact_does_not_hide_down_devices() -> None:
    c = config()
    found = evaluate(c, status(c, {"flags": ["no_contact", "on_battery"]}, down=("nas",)), NOW, {}, STARTED)
    assert {p.key for p in found} == {"ups:ups1:contact", "down:nas"}


def test_children_follow_their_down_parent_into_the_ups_alarm() -> None:
    c = config(guest={"host": "g", "runs_on": {"host": "nas", "vm": "guest"}})
    found = evaluate(c, status(c, {"flags": ["on_battery"]}, down=("nas", "guest")), NOW, {}, STARTED)
    assert [p.key for p in found] == ["ups:ups1:battery"]
    assert found[0].hidden == ["guest", "nas"]


def test_a_battery_to_replace_and_no_contact() -> None:
    c = config()
    found = evaluate(
        c, status(c, {"flags": ["online", "replace_battery"], "battery_date": "2019-01-01"}), NOW, {}, STARTED
    )
    assert [p.key for p in found] == ["ups:ups1:replace"] and "2019-01-01" in found[0].detail
    found = evaluate(c, status(c, {"flags": ["no_contact"]}), NOW, {}, STARTED)
    assert [p.key for p in found] == ["ups:ups1:contact"]


def test_states_in_a_word() -> None:
    assert ups.state(["online", "replace_battery"]) == "replace_battery"
    assert ups.state(["on_battery", "low_battery"]) == "low_battery"
    assert ups.state([]) == "unknown"


def test_missing_or_stale_ups_readings_do_not_claim_health_or_resolve_alarms() -> None:
    c = config()
    st = status(c, {"flags": ["online"]})
    assert ups.current_state(ups.reading(c, st, "ups1"), NOW) == "online"
    st["sv1"].host_checked = NOW - timedelta(hours=1)
    assert ups.current_state(ups.reading(c, st, "ups1"), NOW) == "no_contact"
    unjudged = set()
    assert not evaluate(c, st, NOW, {}, STARTED, unjudged)
    assert unjudged == {"ups:ups1:battery", "ups:ups1:replace", "ups:ups1:contact"}
    st["sv1"].host, st["sv1"].host_checked = {}, NOW
    unjudged.clear()
    found = evaluate(c, st, NOW, {}, STARTED, unjudged)
    assert [p.key for p in found] == ["ups:ups1:contact"]
    assert unjudged == {"ups:ups1:battery", "ups:ups1:replace"}


def test_back_on_mains_what_is_still_down_stays_in_its_alarm() -> None:
    """Minutes after the outage what went down with it may still be coming up: one alarm."""
    c = config()
    back = (NOW - timedelta(minutes=3)).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    found = evaluate(
        c, status(c, {"flags": ["online"], "off_battery_since": back}, down=("nas",)), NOW, {}, STARTED
    )
    assert [p.key for p in found] == ["ups:ups1:battery"] and found[0].hidden == ["nas"]
    assert "back on mains" in found[0].title
    long_ago = (NOW - timedelta(hours=2)).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    found = evaluate(
        c, status(c, {"flags": ["online"], "off_battery_since": long_ago}, down=("nas",)), NOW, {}, STARTED
    )
    assert [p.key for p in found] == ["down:nas"]


def test_the_api_and_the_power_links(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    path = tmp_path / "config.yaml"
    path.write_text(
        "devices:\n"
        "  sv1: {host: 127.0.0.1, kind: server, powered_by: ups1}\n"
        "  nas: {host: 127.0.0.2}\n"
        "  ups1: {kind: ups, name: UPS, ups: {via: sv1}}\n"
        f"users:\n  ada: {{role: admin, password: '{hash_password('pw for tests')}'}}\n"
    )
    h = {"X-Kastellan": "1"}
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        assert c.post("/api/auth/login", json={"user": "ada", "password": "pw for tests"}, headers=h)
        hub = c.app.state.hub
        hub.monitor.status["sv1"].host = {"ups": [{"flags": ["on_battery"], "charge": 70.0}]}
        hub.monitor.status["sv1"].host_checked = datetime.now(UTC)
        d = {x["id"]: x for x in c.get("/api/devices").json()}
        assert d["ups1"]["ups"]["state"] == "on_battery" and d["ups1"]["ups"]["charge"] == 70.0
        assert d["ups1"]["powers"] == ["sv1"] and d["ups1"]["host"] == ""
        assert d["sv1"]["powered_by"] == {"ups": "ups1", "state": "on_battery"}
        assert d["sv1"]["reads_ups"] == [{"device": "ups1", "state": "on_battery"}]
        version = c.get("/api/config").json()["version"]
        r = c.put("/api/devices/ups1/powered", json={"devices": ["nas"], "version": version}, headers=h)
        assert r.status_code == 200, r.text
        text = path.read_text()
        assert (
            "nas: {host: 127.0.0.2, powered_by: ups1}" in text or "powered_by: ups1" in text.split("nas:")[1]
        )
        assert "powered_by" not in text.split("nas:")[0]  # sv1 no longer
        stale = c.put("/api/devices/ups1/powered", json={"devices": [], "version": version}, headers=h)
        assert stale.status_code == 409
        not_ups = c.put(
            "/api/devices/nas/powered", json={"devices": [], "version": r.json()["version"]}, headers=h
        )
        assert not_ups.status_code == 404
