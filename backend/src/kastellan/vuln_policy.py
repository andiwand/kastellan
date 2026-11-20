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
that container, and a WUD trigger is configured. Once per new image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alarms import Severity
from .config import Device


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
    """Why this container may be updated automatically now, or None (the usual answer)."""
    if not (device.auto_update and device.exposed and device.docker and device.docker.trigger):
        return None
    if not container.get("update") or container.get("unchecked"):
        return None
    reasons = [
        f["id"]
        for f in findings
        if f.get("confidence") == "exact" and (urgent(f) or f.get("severity") == "high")
    ]
    return f"{', '.join(reasons[:3])} on an internet-facing device" if reasons else None
