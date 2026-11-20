"""What a vulnerability finding means for a device: an alarm, a message, or an automatic update.

The rule decided on 2026-11-20 (PLAN.md): updates are suggested, not installed; automatically
only when a known vulnerability meets a device that faces the internet. Concretely:

| finding (confirmed)            | exposed device | internal device |
|--------------------------------|----------------|-----------------|
| exploited (CISA KEV) or ≥ 9.0  | critical alarm | warning alarm   |
| high (7.0 to 8.9)              | warning alarm  | info message    |
| medium, low, unknown           | shown only     | shown only      |

An unconfirmed finding (the distribution may have patched it) is an info message at most.

Automatic update: only a container, only with `auto_update: true` on the device, only when the
device is exposed, a confirmed finding is exploited or high/critical, WUD has a newer image for
that container within the same major version that includes the fix (when the fix is known), and
a WUD trigger is configured. Once per new image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alarms import Severity
from .config import Device
from .vulns import version_key


@dataclass(frozen=True)
class Assessment:
    alarm: Severity | None  # open an alarm with this severity
    message: bool  # send an info message (once)


def urgent(finding: dict[str, Any]) -> bool:
    return bool(finding.get("exploited")) or finding.get("severity") == "critical"


def assess(device: Device, finding: dict[str, Any]) -> Assessment:
    if finding.get("confidence") != "exact":
        return Assessment(None, urgent(finding) or finding.get("severity") == "high")
    if urgent(finding):
        return Assessment(Severity.CRITICAL if device.exposed else Severity.WARNING, False)
    if finding.get("severity") == "high":
        return Assessment(Severity.WARNING, False) if device.exposed else Assessment(None, True)
    return Assessment(None, False)


def auto_update_reason(
    device: Device, container: dict[str, Any], findings: list[dict[str, Any]]
) -> str | None:
    """Why this container may be updated automatically now, or None (the usual answer).

    Besides the device's consent and exposure: the update must stay within the same major
    version (16 → 17 can need a migration, e.g. a database's data directory), and where the fixed
    version is known, the new version must include it."""
    if not (device.auto_update and device.exposed and device.docker and device.docker.trigger):
        return None
    if not container.get("update") or container.get("unchecked"):
        return None
    current, target = str(container.get("tag") or ""), str(container.get("update_to") or "")
    same_tag = target == "newer build"
    if not same_tag:
        a, b = version_key(current), version_key(target)
        if not a or not b or a[0] != b[0] or b <= a:
            return None
    reasons = [
        f["id"]
        for f in findings
        if f.get("confidence") == "exact"
        and (urgent(f) or f.get("severity") == "high")
        and (same_tag or not f.get("fixed_in") or version_key(str(f["fixed_in"])) <= version_key(target))
    ]
    return f"{', '.join(reasons[:3])} on an internet-facing device" if reasons else None
