"""UPS devices (kind ups): what their host's collector reads of them (`ups: {via}`), their state
in a word, and which devices they power (`powered_by`). Used by the API and the alarms."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import Config
from .monitor import DeviceStatus

# The flags, worst first: the one a UPS is shown by.
STATES = ("no_contact", "low_battery", "on_battery", "replace_battery", "online", "unknown")
MAX_AGE = timedelta(minutes=15)


def reading(config: Config, status: dict[str, DeviceStatus], id: str) -> dict[str, Any] | None:
    """The UPS `id` as its host's report has it (with `checked`: when that host was read), or None
    before the host has reported. A missing UPS in a report means no contact. With NUT,
    `ups.name` picks which; else the host's first."""
    link = config.devices[id].ups
    st = status.get(link.via) if link else None
    found = (st.host or {}).get("ups") if st else None
    if not link or not st or st.host is None:
        return None
    found = found or []
    chosen = (
        next((u for u in found if u.get("name") == link.name), None) if link.name else next(iter(found), None)
    )
    if chosen is None:
        chosen = {"name": link.name, "flags": ["no_contact"]}
    return {**chosen, "checked": st.host_checked}


def current_state(reading: dict[str, Any] | None, now: datetime) -> str:
    """Old readings cannot say the UPS is still healthy (or still on battery)."""
    if reading is None:
        return "unknown"
    checked = reading.get("checked")
    if checked is None or now - checked > MAX_AGE:
        return "no_contact"
    return state(reading["flags"])


def state(flags: list[str]) -> str:
    """The flag that matters most: no_contact, low_battery, on_battery, replace_battery, online."""
    return next((s for s in STATES if s in flags), "unknown")


def powered(config: Config, id: str) -> list[str]:
    """The devices hanging on UPS `id` (powered_by), sorted."""
    return sorted(d for d, dev in config.devices.items() if dev.powered_by == id)


def since(text: str | None) -> datetime | None:
    """apcupsd's times ("2026-11-21 10:00:00 +0100"); None when there is none or it doesn't read."""
    try:
        return datetime.strptime(text or "", "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None
