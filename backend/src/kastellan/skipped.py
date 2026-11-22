"""Container updates an admin said aren't needed (`<data>/updates-skipped.json`): per device and
container, the one offer passed. Updates stops showing it and no notification asks again; a newer
offer (another tag, or another build of the same tag) shows as usual."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def offer(container: dict[str, Any]) -> str | None:
    """What a container's update offer is: its newer tag, or which new build of its own tag."""
    if not container.get("update") or container.get("unchecked"):
        return None
    to = container.get("update_to") or "newer build"
    return f"newer build {container.get('update_digest') or ''}".strip() if to == "newer build" else to


def identity(container: dict[str, Any]) -> str | None:
    """An offer belongs to this image and running tag, even if the container's name is reused."""
    what = offer(container)
    return json.dumps([container.get("image"), container.get("tag"), what]) if what else None


class Skipped:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            device: {key: value for key, value in entries.items() if isinstance(value, str)}
            for device, entries in data.items()
            if isinstance(entries, dict)
        }

    def skip(self, device: str, key: str, what: str) -> None:
        with self._lock:
            data = self._read()
            data.setdefault(device, {})[key] = what
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(self.path)

    def is_skipped(self, device: str, container: dict[str, Any]) -> bool:
        what = identity(container)
        key = container.get("key", container.get("name"))
        return what is not None and self._read().get(device, {}).get(str(key)) == what

    def mark(self, device: str, containers: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        """The containers as the app sees them: `skipped` on an offer passed."""
        if not containers:
            return containers
        passed = self._read().get(device, {})
        return [
            {
                **c,
                "skipped": (w := identity(c)) is not None
                and passed.get(str(c.get("key", c.get("name")))) == w,
            }
            for c in containers
        ]
