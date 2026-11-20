"""The audit log: who did what, when, from where. One JSON object per line in <data>/audit.log.

Everything that changes a device or hands out access goes here: sign-ins (and failed ones),
sign-outs, terminal and remote desktop sessions, later updates and jobs. The file is only
appended to; nothing in Kastellan rewrites it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class Audit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(self, action: str, user: str | None, address: str | None = None, **details: Any) -> None:
        entry = {
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "action": action,
            "user": user,
            "address": address,
            **details,
        }
        line = json.dumps(entry, default=str, ensure_ascii=False)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                new = not self.path.exists()
                with self.path.open("a") as f:
                    f.write(line + "\n")
                if new:
                    os.chmod(self.path, 0o600)
            except OSError as e:
                log.error("cannot write the audit log: %s (%s)", e, line)

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """The newest entries, newest first."""
        try:
            with self.path.open() as f:
                lines = deque(f, maxlen=limit)
        except OSError:
            return []
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a line cut short by a crash
        return out
