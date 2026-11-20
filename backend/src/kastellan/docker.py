"""Docker containers of a host and their updates, from WUD (What's Up Docker) for now.

    devices:
      sv1:
        docker:
          wud: http://192.168.15.10:3000

WUD already watches the registries; Kastellan reads its API (GET /api/containers, read-only) and
shows, per container: image and tag, state, and whether a newer image exists, or why that can't
be known: WUD can't read every registry (linuxserver's lscr.io needs its own WUD setting), and for
tags like `latest` it sees updates only when it watches the image digest.
The agent will be the other source later (PLAN.md); this module is where both meet.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Container:
    name: str
    image: str  # "andiwand/norn"
    tag: str  # "0.10.0", "latest"
    image_id: str  # short id of the image it runs; changes with every pulled update
    state: str  # running, exited, ...
    update: bool
    update_to: str | None  # the newer tag, or "newer digest" for the same tag
    unchecked: str | None  # why WUD can't tell, or None
    registry: str | None

    @property
    def running_version(self) -> str:
        """What the version log keeps: the image, its tag, and which build of that tag."""
        return f"{self.image}:{self.tag} ({self.image_id})" if self.image_id else f"{self.image}:{self.tag}"


def from_wud(item: dict[str, Any]) -> Container:
    image = item.get("image") or {}
    tag = (image.get("tag") or {}).get("value") or "?"
    result = item.get("result") or {}
    kind = (item.get("updateKind") or {}).get("kind")
    update = bool(item.get("updateAvailable"))
    update_to = None
    if update:
        new_tag = result.get("tag")
        update_to = new_tag if new_tag and new_tag != tag else "newer build" if kind == "digest" else new_tag
    error = (item.get("error") or {}).get("message") if isinstance(item.get("error"), dict) else None
    tag_info = image.get("tag") or {}
    digest = image.get("digest") or {}
    if not error and not update and not tag_info.get("semver") and not digest.get("watch"):
        # A tag like latest or alpine only changes its content; WUD sees that only when it
        # watches the digest. Without that, "no update" means "not looked", not "up to date".
        error = f"WUD doesn't watch the digest of :{tag} (label wud.watch.digest=true)"
    image_id = str(image.get("id") or "").removeprefix("sha256:")[:12]
    return Container(
        name=str(item.get("displayName") or item.get("name") or "?")[:100],
        image=str(image.get("name") or "?")[:200],
        tag=str(tag)[:100],
        image_id=image_id,
        state=str(item.get("status") or "?")[:40],
        update=update,
        update_to=str(update_to)[:100] if update_to else None,
        unchecked=str(error)[:200] if error else None,
        registry=((image.get("registry") or {}).get("name")),
    )


async def read_wud(url: str) -> list[Container]:
    """All containers WUD watches; raises httpx.HTTPError or ValueError when it can't be read."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url.rstrip("/") + "/api/containers")
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        raise ValueError("unexpected answer from WUD")
    return sorted((from_wud(i) for i in items if isinstance(i, dict)), key=lambda c: c.name.lower())


def as_dicts(containers: list[Container]) -> list[dict[str, Any]]:
    return [asdict(c) for c in containers]
