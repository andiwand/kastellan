from kastellan.alarms import Severity
from kastellan.config import Device
from kastellan.vuln_policy import assess, auto_update_reason


def device(**kw) -> Device:
    return Device.model_validate({"host": "x", **kw})


EXPLOITED = {"id": "CVE-1", "severity": "medium", "exploited": True, "confidence": "exact"}
CRITICAL = {"id": "CVE-2", "severity": "critical", "confidence": "exact"}
HIGH = {"id": "CVE-3", "severity": "high", "confidence": "exact"}
MEDIUM = {"id": "CVE-4", "severity": "medium", "confidence": "exact"}
UPSTREAM = {"id": "CVE-5", "severity": "critical", "exploited": True, "confidence": "upstream"}


def test_assess_follows_the_table() -> None:
    exposed, internal = device(exposed=True), device()
    assert assess(exposed, EXPLOITED).alarm is Severity.CRITICAL
    assert assess(internal, EXPLOITED).alarm is Severity.WARNING
    assert assess(exposed, CRITICAL).alarm is Severity.CRITICAL
    assert assess(exposed, HIGH).alarm is Severity.WARNING
    assert assess(internal, HIGH).alarm is None and assess(internal, HIGH).message
    assert assess(exposed, MEDIUM).alarm is None and not assess(exposed, MEDIUM).message
    # The distribution may have fixed it: a message at most, never an alarm.
    assert assess(exposed, UPSTREAM).alarm is None and assess(exposed, UPSTREAM).message


def test_auto_update_only_when_everything_agrees() -> None:
    allowed = device(
        exposed=True, auto_update=True, docker={"wud": "http://w:3000", "trigger": "docker.local"}
    )
    container = {
        "name": "ha",
        "update": True,
        "unchecked": None,
        "tag": "2026.11.2",
        "update_to": "2026.11.3",
    }
    assert auto_update_reason(allowed, container, [CRITICAL]) is not None
    assert auto_update_reason(allowed, container, [MEDIUM]) is None  # not serious enough
    assert auto_update_reason(allowed, container, [UPSTREAM]) is None  # not confirmed
    assert auto_update_reason(allowed, {**container, "update": False}, [CRITICAL]) is None  # no fix
    major = {**container, "tag": "16.4.0", "update_to": "17.0.1"}
    assert auto_update_reason(allowed, major, [CRITICAL]) is None  # 16 → 17 may need a migration
    not_fixed = {**CRITICAL, "fixed_in": "2026.12.0"}
    assert auto_update_reason(allowed, container, [not_fixed]) is None  # 2026.11.3 doesn't fix it
    rebuild = {**container, "tag": "latest", "update_to": "newer build"}
    assert auto_update_reason(allowed, rebuild, [CRITICAL]) is not None
    off = device(exposed=True, docker={"wud": "http://w:3000", "trigger": "docker.local"})
    assert auto_update_reason(off, container, [CRITICAL]) is None  # auto_update not set
    internal = device(auto_update=True, docker={"wud": "http://w:3000", "trigger": "docker.local"})
    assert auto_update_reason(internal, container, [CRITICAL]) is None  # not internet-facing
    no_trigger = device(exposed=True, auto_update=True, docker={"wud": "http://w:3000"})
    assert auto_update_reason(no_trigger, container, [CRITICAL]) is None


async def test_hub_turns_findings_into_alarms(tmp_path) -> None:
    from kastellan.hub import Hub
    from kastellan.monitor import CheckStatus, DeviceStatus
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  gw:\n    host: 1.2.3.4\n    exposed: true\n    checks: [{tcp: 22, name: ssh}]\n"
    )
    hub = Hub(Settings(config=config, data=tmp_path))
    hub.reload()
    status = hub.monitor.status["gw"] = DeviceStatus(checks={"ssh": CheckStatus(software="OpenSSH 9.8")})
    [(where, product)] = hub._products(status)
    assert where == "service ssh" and product.cpe and ":openssh:9.8:" in product.cpe
    status.vulns = {"items": [{"where": where, "product": product.label, "findings": [EXPLOITED, MEDIUM]}]}
    [problem] = hub._vuln_problems()
    assert problem.severity is Severity.CRITICAL and "exploited CVE-1" in problem.title
    await hub.monitor.stop()


async def test_vuln_alarms_survive_a_restart(tmp_path) -> None:
    """Review: every restart sent "Resolved" and then re-alarmed every vulnerability."""
    from datetime import UTC, datetime, timedelta

    from kastellan.alarms import RESOLVE_AFTER
    from kastellan.hub import Hub
    from kastellan.monitor import CheckStatus, DeviceStatus
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  gw:\n    host: 1.2.3.4\n    exposed: true\n    checks: [{tcp: 22, name: ssh}]\n"
    )
    hub = Hub(Settings(config=config, data=tmp_path))
    hub.reload()
    status = hub.monitor.status["gw"] = DeviceStatus(checks={"ssh": CheckStatus(software="OpenSSH 9.8")})
    status.vulns = {
        "items": [{"where": "service ssh", "product": "OpenSSH 9.8", "findings": [EXPLOITED]}],
        "unknown": [],
    }
    now = datetime.now(UTC)
    assert len(hub.alarms.update(hub._vuln_problems(), now)) == 1  # opened
    await hub.monitor.stop()

    # The restart: a new hub, nothing swept yet.
    again = Hub(Settings(config=config, data=tmp_path))
    again.reload()
    again.monitor.status["gw"] = DeviceStatus(checks={"ssh": CheckStatus(software="OpenSSH 9.8")})
    later = now + timedelta(seconds=RESOLVE_AFTER + 60)
    problems = again._vuln_problems() + again.alarms.carry(again._vuln_keys_unknown())
    assert again.alarms.update(problems, now + timedelta(seconds=30)) == []
    assert again.alarms.update(problems, later) == []  # no "Resolved"
    assert "vuln:gw:service ssh:CVE-1" in again.alarms.open
    await again.monitor.stop()


def test_exposure_per_service() -> None:
    d = device(checks=[{"tcp": 443, "name": "https", "exposed": True}, {"tcp": 22, "name": "ssh"}])
    # What the exposed service runs is urgent; what the inside-only one runs isn't.
    assert assess(d, CRITICAL, "service https").alarm is Severity.CRITICAL
    assert assess(d, CRITICAL, "service ssh").alarm is Severity.WARNING
    # The firmware and the containers of a device with an exposed service count as exposed.
    assert assess(d, CRITICAL, "firmware").alarm is Severity.CRITICAL
    assert d.faces_internet() and not device(checks=[{"tcp": 22}]).faces_internet()
    # The whole device marked: everything counts.
    assert (
        assess(device(exposed=True, checks=[{"tcp": 22}]), CRITICAL, "service tcp:22").alarm
        is Severity.CRITICAL
    )


def test_an_exposed_service_is_a_way_in() -> None:
    from kastellan.networks import public_ways

    d = device(host="10.0.0.5", checks=[{"tcp": 443, "name": "https", "exposed": True}, {"tcp": 22}])
    assert [w["check"] for w in public_ways(d, False, {})] == ["https"]


def test_a_public_name_counts_and_auto_update_needs_the_whole_device() -> None:
    d = device(checks=[{"http": "https://media.example.org", "name": "jellyfin"}, {"tcp": 22, "name": "ssh"}])
    assert d.faces_internet("service jellyfin") and not d.faces_internet("service ssh")
    one = device(auto_update=True, act={}, checks=[{"tcp": 443, "exposed": True}])
    c = {"update": True, "tag": "1.0.0", "update_to": "1.0.1"}
    assert auto_update_reason(one, c, [CRITICAL]) is None
