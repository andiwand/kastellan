"""New abilities Kastellan offers on a device that its login there doesn't have yet (the actor on
a Linux host, the OpenWrt login), for the Updates page: set up again to tick them, or say
they aren't needed.

`ADDED`: abilities that came after the first setups, by kind. One counts as new on a device when
it isn't installed there, wasn't left out at its last setup (that was a choice), and wasn't
dismissed (`<data>/abilities-offered.json`: per device and kind, the abilities seen and passed).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ADDED = {
    "actor": ("rollback-container", "container-logs", "unit-logs", "journal", "docker-logs"),
    "openwrt": ("logs", "backup", "scan"),
}


class Offered:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, list[str]]]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def passed(self, device: str, kind: str, abilities: set[str] | list[str]) -> None:
        """These were offered and not wanted (left out at a setup, or dismissed)."""
        with self._lock:
            data = self._read()
            entry = data.setdefault(device, {})
            entry[kind] = sorted(set(entry.get(kind, [])) | set(abilities))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.path)

    def new(self, device: str, kind: str, installed: list[str] | None) -> list[str]:
        if installed is None:  # not known yet
            return []
        gone = set(self._read().get(device, {}).get(kind, []))
        return [a for a in ADDED[kind] if a not in installed and a not in gone]
