"""What software each service ran, and when that changed: <data>/versions.log, one JSON object
per line, written when a check first sees a service's software and whenever it changes.

    {"time": "...", "device": "sv1", "check": "ssh", "from": "OpenSSH 10.1p1 (Ubuntu)",
     "to": "OpenSSH 10.2p1 (Ubuntu)"}

Small by nature (a line per update), so it is read whole.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Versions:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _entries(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
                entry["time"] = datetime.fromisoformat(entry["time"])
                out.append(entry)
            except (ValueError, KeyError, TypeError):
                continue  # a line cut short by a crash
        return out

    def record(self, time: datetime, device: str, check: str, old: str | None, new: str) -> None:
        line = json.dumps(
            {
                "time": time.isoformat(timespec="seconds"),
                "device": device,
                "check": check,
                "from": old,
                "to": new,
            },
            ensure_ascii=False,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("cannot write the version log: %s", e)

    def latest(self) -> dict[tuple[str, str], tuple[str, datetime]]:
        """Per (device, check): the software last seen and since when."""
        out: dict[tuple[str, str], tuple[str, datetime]] = {}
        for e in self._entries():
            out[(e["device"], e["check"])] = (e["to"], e["time"])
        return out

    def changes(self, since: datetime, device: str | None = None) -> list[dict[str, Any]]:
        """Real changes (not first sightings) since a time, newest first."""
        return [
            e
            for e in reversed(self._entries())
            if e["from"] is not None and e["time"] >= since and (device is None or e["device"] == device)
        ]
