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


def reading(
    config: Config,
    status: dict[str, DeviceStatus],
    id: str,
    switched: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """The UPS `id` as its host's report has it (with `checked`: when that host was read), or None
    before the host has reported. A missing UPS in a report means no contact. With NUT,
    `ups.name` picks which; else the host's first one it has contact with. `switched`: the
    hub's memory of when each UPS switched (NUT tells no times; apcupsd does), kept up to date."""
    link = config.devices[id].ups
    st = status.get(link.via) if link else None
    if not link or not st or st.host is None:
        return None
    found = st.host.get("ups") or []
    if link.name:
        chosen = next((u for u in found if u.get("name") == link.name), None)
    else:
        reached = [u for u in found if "no_contact" not in (u.get("flags") or [])]
        chosen = next(iter(reached or found), None)
    if chosen is None:
        chosen = {"name": link.name, "flags": ["no_contact"]}
    r = {**chosen, "checked": st.host_checked}
    return _with_times(switched, id, r) if switched is not None else r


def _on_battery(flags: list[str]) -> bool:
    return bool({"on_battery", "low_battery"} & set(flags))


def _with_times(switched: dict[str, dict[str, Any]], id: str, r: dict[str, Any]) -> dict[str, Any]:
    """The switch times NUT doesn't tell, from when the hub saw them. Known only while it runs;
    after a restart, the first reading stands in ("at least since")."""
    checked = r.get("checked")
    if checked is None or "no_contact" in r["flags"]:
        return r
    battery = _on_battery(r["flags"])
    seen = switched.get(id)
    if seen is None or seen["battery"] != battery:
        seen = switched[id] = {
            "battery": battery,
            "at": checked,
            "on_at": checked if battery else (seen or {}).get("on_at"),
        }
    fmt = "%Y-%m-%d %H:%M:%S %z"
    if seen["on_at"] and not r.get("on_battery_since"):
        r["on_battery_since"] = seen["on_at"].strftime(fmt)
    if not battery and seen["on_at"] and not r.get("off_battery_since"):
        r["off_battery_since"] = seen["at"].strftime(fmt)
    return r


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
