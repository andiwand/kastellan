"""What each device runs, and when that changed: <data>/versions.log, one JSON object per line,
written at the first sighting and at every change. Keys are a service's check name, `@firmware`
(the device's firmware) or `@container <key>` (a container's running image).

    {"time": "...", "device": "sv1", "check": "ssh", "from": "OpenSSH 10.1p1 (Ubuntu)",
     "to": "OpenSSH 10.2p1 (Ubuntu)"}

Small by nature (a line per update), so it is read whole; what each runs now is kept in memory.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Versions:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._latest: dict[tuple[str, str], tuple[str, datetime, str | None]] | None = None

    def _entries(self) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                entry = json.loads(line)
                time = datetime.fromisoformat(entry["time"])
                if time.tzinfo is None or not all(k in entry for k in ("device", "check", "from", "to")):
                    continue
                entry["time"] = time
                entry.setdefault("target", None)
                out.append(entry)
            except (ValueError, KeyError, TypeError, AttributeError):
                continue  # a line cut short by a crash, or edited by hand
        return out

    def record(
        self, time: datetime, device: str, check: str, old: str | None, new: str, target: str | None = None
    ) -> None:
        entry = {
            "time": time.isoformat(timespec="seconds"),
            "device": device,
            "check": check,
            "from": old,
            "to": new,
        }
        if target is not None:
            entry["target"] = target
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("cannot write the version log: %s", e)
            return
        if self._latest is not None:  # as read back: whole seconds
            self._latest[(device, check)] = (new, datetime.fromisoformat(entry["time"]), target)

    def compact(self, keep_days: int, now: datetime | None = None) -> None:
        """Keep the changes of the last `keep_days`, and the latest entry of every check (what it
        runs now), however old. Done at start, so the file stays small."""
        entries = self._entries()
        if not entries:
            return
        cutoff = (now or datetime.now(UTC)) - timedelta(days=keep_days)
        last = {(e["device"], e["check"]): i for i, e in enumerate(entries)}
        keep = [
            e for i, e in enumerate(entries) if e["time"] >= cutoff or last[(e["device"], e["check"])] == i
        ]
        if len(keep) == len(entries):
            return
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(
                "".join(
                    json.dumps({**e, "time": e["time"].isoformat(timespec="seconds")}) + "\n" for e in keep
                )
            )
            tmp.replace(self.path)
        except OSError as e:
            log.warning("cannot compact the version log: %s", e)

    def latest(self) -> dict[tuple[str, str], tuple[str, datetime, str | None]]:
        """Per (device, check): the software last seen, since when, and what the check pointed at.
        Read from the file once, then kept up to date by record()."""
        if self._latest is None:
            self._latest = {
                (e["device"], e["check"]): (e["to"], e["time"], e["target"]) for e in self._entries()
            }
        return dict(self._latest)

    def changes(self, since: datetime, device: str | None = None) -> list[dict[str, Any]]:
        """Real changes (not first sightings) since a time, newest first."""
        return [
            e
            for e in reversed(self._entries())
            if e["from"] is not None and e["time"] >= since and (device is None or e["device"] == device)
        ]
