"""What a vulnerability finding means for a device: an alarm, a message, or an automatic update.

The rule decided on 2026-11-20 (PLAN.md): updates are suggested, not installed; automatically
only when a known vulnerability meets a device that faces the internet. Concretely:

| finding (confirmed)            | exposed        | internal        |
|--------------------------------|----------------|-----------------|
| exploited (CISA KEV) or ≥ 9.0  | critical alarm | warning alarm   |
| high (7.0 to 8.9)              | warning alarm  | info message    |
| medium, low, unknown           | shown only     | shown only      |

Exposed: the device as a whole (`exposed: true`), or the service the finding is in (a check's
`exposed: true`); firmware and containers count as exposed when any service of the device is.
An unconfirmed finding (unknown patch level or a possible distribution fix) is an info message at most.

Automatic update: only a container, only with `auto_update: true` on the device, only when the
device as a whole is marked exposed (`exposed: true`; one exposed service isn't enough), a
confirmed finding is exploited or high/critical, there is a newer image for that container
within the same major version that includes the fix (when the fix is known), and Kastellan may
update it (the actor, or a WUD trigger). Once per new image.
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


def assess(device: Device, finding: dict[str, Any], where: str | None = None) -> Assessment:
    """`where`: what the finding is in ("service ssh", "firmware", "container x"); exposure is
    decided for that (Device.faces_internet)."""
    exposed = device.faces_internet(where)
    if finding.get("confidence") != "exact":
        return Assessment(None, urgent(finding) or finding.get("severity") == "high")
    if urgent(finding):
        return Assessment(Severity.CRITICAL if exposed else Severity.WARNING, False)
    if finding.get("severity") == "high":
        return Assessment(Severity.WARNING, False) if exposed else Assessment(None, True)
    return Assessment(None, False)


def auto_update_reason(
    device: Device, container: dict[str, Any], findings: list[dict[str, Any]]
) -> str | None:
    """Why this container may be updated automatically now, or None (the usual answer).

    Besides the device's consent and exposure: the update must stay within the same major
    version (16 → 17 can need a migration, e.g. a database's data directory), and where the fixed
    version is known, the new version must include it."""
    can = device.act is not None or bool(device.docker and device.docker.trigger)  # actor or WUD
    # The device as a whole: one forwarded port doesn't make every container fair game.
    if not (device.auto_update and device.exposed and can):
        return None
    if not container.get("update") or container.get("unchecked"):
        return None
    current, target = str(container.get("tag") or ""), str(container.get("update_to") or "")
    # A newer build of the same tag is the same version: it holds no fix newer than that.
    same_tag = target == "newer build"
    new = current if same_tag else target
    if not same_tag:
        a, b = version_key(current), version_key(target)
        if not a or not b or a[0] != b[0] or b <= a:
            return None
    reasons = [
        f["id"]
        for f in findings
        if f.get("confidence") == "exact"
        and (urgent(f) or f.get("severity") == "high")
        and (not f.get("fixed_in") or version_key(str(f["fixed_in"])) <= version_key(new))
    ]
    return f"{', '.join(reasons[:3])} on an internet-facing device" if reasons else None
