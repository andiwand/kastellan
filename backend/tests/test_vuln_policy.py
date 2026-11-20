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
    container = {"name": "ha", "update": True, "unchecked": None}
    assert auto_update_reason(allowed, container, [CRITICAL]) is not None
    assert auto_update_reason(allowed, container, [MEDIUM]) is None  # not serious enough
    assert auto_update_reason(allowed, container, [UPSTREAM]) is None  # not confirmed
    assert auto_update_reason(allowed, {**container, "update": False}, [CRITICAL]) is None  # no fix
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
