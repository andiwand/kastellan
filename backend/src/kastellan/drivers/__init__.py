"""Drivers: what Kastellan can learn from (and later do with) a kind of device beyond checks.

A driver is chosen per device in config.yaml (`driver: fritzbox`) and registered here by name.
For now drivers report `info()`: model, name, firmware. They use only what the device answers
without credentials; drivers that need an account come with stored secrets (see PLAN.md).
"""

from __future__ import annotations

from .base import DeviceInfo, Driver
from .fritzbox import FritzBox

DRIVERS: dict[str, type[Driver]] = {
    FritzBox.name: FritzBox,
}

__all__ = ["DRIVERS", "DeviceInfo", "Driver"]
