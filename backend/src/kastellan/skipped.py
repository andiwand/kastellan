"""Updates an admin said aren't needed (`<data>/updates-skipped.json`): per device, the one offer
passed, for each container (by its key) and for the device's own (`@firmware`, `@reboot`,
`@packages`). Updates stops showing it and no notification asks again; a newer offer (another
tag or build; another firmware release; a reboot for other packages; other system updates)
shows as usual."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .registry import NEWER_BUILD


def offer(container: dict[str, Any]) -> str | None:
    """What a container's update offer is: its newer tag, or which new build of its own tag."""
    if not container.get("update") or container.get("unchecked"):
        return None
    to = container.get("update_to") or NEWER_BUILD
    return f"{NEWER_BUILD} {container.get('update_digest') or ''}".strip() if to == NEWER_BUILD else to


def identity(container: dict[str, Any]) -> str | None:
    """An offer belongs to this image and running tag, even if the container's name is reused."""
    what = offer(container)
    return json.dumps([container.get("image"), container.get("tag"), what]) if what else None


DEVICE_KINDS = ("firmware", "reboot", "packages")


def device_offers(router: dict[str, Any] | None, host: dict[str, Any] | None) -> dict[str, str]:
    """The device's own update offers now, each as what makes it this one: the OpenWrt release
    to go to, the packages a reboot is for, how many system updates (and for security)."""
    out: dict[str, str] = {}
    fw = (router or {}).get("firmware") or {}
    if fw.get("next_step"):
        out["firmware"] = f"OpenWrt {fw['next_step']}"
    updates = (host or {}).get("updates") or {}
    if updates.get("reboot"):
        out["reboot"] = "reboot for " + " ".join(sorted(str(p) for p in updates.get("reboot_for") or []))
    if updates.get("available"):
        out["packages"] = f"{updates['available']} updates, {updates.get('security') or 0} for security"
    return out


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

    def device_skipped(
        self, device: str, router: dict[str, Any] | None, host: dict[str, Any] | None
    ) -> list[str]:
        """Which of the device's own offers now were said to be not needed (DEVICE_KINDS)."""
        passed = self._read().get(device, {})
        return [k for k, what in device_offers(router, host).items() if passed.get(f"@{k}") == what]
