"""A device's own services over time: whether each of an OpenWrt device's init scripts ran at
every look (`rc list`), kept like the checks' history (`<data>/services`: a CSV file a day, the
same retention), so the device page shows their uptime and since when, as for its checks.

Which services: the ones worth seeing (running, started at boot, or on Kastellan's list), the
same as the page shows. A Linux host's systemd units aren't here: its collector only names the
failed ones."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .history import History, Row


def worth_seeing(router: dict[str, Any]) -> list[tuple[str, bool]]:
    """(name, running) of a router report's services that the page shows."""
    allowed = set(router.get("services_allowed") or [])
    return [
        (str(s["name"]), bool(s.get("running")))
        for s in router.get("services") or []
        if isinstance(s, dict)
        and s.get("name")
        and (s.get("running") or s.get("enabled") or s["name"] in allowed)
    ]


class ServiceStates:
    def __init__(self, folder: Path) -> None:
        self.history = History(folder)
        # (device, service) -> (running, since, since_exact)
        self._state: dict[tuple[str, str], tuple[bool, datetime, bool]] = {}

    def restore(self, rows: Iterable[Row]) -> None:
        """Since when each service has been as it is, from the history (rows in time order)."""
        for row in rows:
            key = (row.device, row.check)
            known = self._state.get(key)
            if known is None:
                self._state[key] = (row.up, row.time, False)  # began then or earlier
            elif known[0] != row.up:
                self._state[key] = (row.up, row.time, True)

    def note(self, device: str, router: dict[str, Any], now: datetime) -> None:
        """One look at a router's services: kept, and their state's start when it changed."""
        rows = []
        for name, running in worth_seeing(router):
            key = (device, name)
            known = self._state.get(key)
            if known is None or known[0] != running:
                self._state[key] = (running, now, known is not None)
            rows.append(Row(now, device, name, running, None, ""))
        if rows:
            self.history.extend(rows)

    def since(self, device: str) -> dict[str, dict[str, Any]]:
        """Per service of the device: since when it has been as it is."""
        return {
            name: {"since": since.isoformat(timespec="seconds"), "since_exact": exact}
            for (dev, name), (_running, since, exact) in self._state.items()
            if dev == device
        }

    def forget(self, keep: set[str]) -> None:
        """Devices no longer configured (or no longer OpenWrt) leave."""
        self._state = {k: v for k, v in self._state.items() if k[0] in keep}

    def restore_from(self, now: datetime) -> None:
        self.restore(self.history.read(now - timedelta(days=1)))
