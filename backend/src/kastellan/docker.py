"""Docker containers of a host and their updates: from the collector (host/collector.py) with
updates asked of the registries (registry.py), or from WUD (What's Up Docker).

    devices:
      sv1:
        docker:
          wud: http://192.168.15.10:3000

WUD already watches the registries; Kastellan reads its API (GET /api/containers, read-only) and
shows, per container: image and tag, state, and whether a newer image exists, or why that can't
be known: WUD can't read every registry (linuxserver's lscr.io needs its own WUD setting), and for
tags like `latest` it sees updates only when it watches the image digest.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from .registry import NEWER_BUILD, parse

log = logging.getLogger(__name__)


MAX_BYTES = 5 * 1024 * 1024  # WUD's answer for a few hundred containers is far below this
FULL_VERSION = re.compile(r"^v?\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Container:
    key: str  # unique per host: WUD's watcher and the container's name
    wud_id: str  # WUD's id for the container (to run a trigger on it)
    name: str
    image: str  # "andiwand/norn"
    tag: str  # "0.10.0", "latest"
    image_id: str  # short id of the image it runs; changes with every pulled update
    state: str  # running, exited, ...
    update: bool
    update_to: str | None  # the newer tag, or NEWER_BUILD for the same tag
    update_digest: str  # the newer image's digest (short), to tell one rebuild from the next
    unchecked: str | None  # why WUD can't tell (or hasn't yet), or None
    registry: str | None
    previous: str | None = None  # the image kept from before its last update (actor), short id
    # When update_to is a new major version: the newest tag within the running one (or None).
    update_within: str | None = None
    compose: str | None = None  # its compose file (or project), when compose made it
    ports: list[int] = field(default_factory=list)  # the host's ports it publishes (collector)

    @property
    def running_version(self) -> str | None:
        """What the version log keeps: image, tag, and which build of it. None without an id
        (a missing field must not look like an update)."""
        return f"{self.image}:{self.tag} ({self.image_id})" if self.image_id else None


def _obj(value: Any) -> dict[str, Any]:
    """WUD's nested fields, read defensively: anything but an object counts as empty."""
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int) -> str:
    return str(value)[:limit] if value is not None else ""


def from_wud(item: dict[str, Any]) -> Container:
    image = _obj(item.get("image"))
    tag_info = _obj(image.get("tag"))
    digest = _obj(image.get("digest"))
    result = _obj(item.get("result"))
    tag = _text(tag_info.get("value") or "?", 100)
    update = item.get("updateAvailable") is True
    update_to = None
    if update:
        new_tag = result.get("tag")
        update_to = _text(new_tag, 100) if new_tag and new_tag != tag else NEWER_BUILD
    unchecked = _text(_obj(item.get("error")).get("message"), 200) or None
    if not unchecked and not update:
        if not result:
            unchecked = "not checked by WUD yet"
        elif not digest.get("watch") and not tag_info.get("semver"):
            # A tag like latest or alpine only changes its content; WUD sees that only when it
            # watches the digest. Without that, "no update" means "not looked", not "up to date".
            unchecked = f"WUD doesn't watch the digest of :{tag} (label wud.watch.digest=true)"
        elif not digest.get("watch") and not FULL_VERSION.match(tag):
            # :15 or :3.19 is a moving tag: WUD compares tags, so rebuilds of it go unseen.
            unchecked = f"only newer tags are checked, not new builds of :{tag}"
    name = _text(item.get("displayName") or item.get("name") or "?", 100)
    watcher = _text(item.get("watcher") or "local", 50)
    return Container(
        key=name if watcher == "local" else f"{watcher}/{name}",
        wud_id=_text(item.get("id"), 100),
        name=name,
        image=_text(image.get("name") or "?", 200),
        tag=tag,
        image_id=_text(image.get("id") or "", 80).removeprefix("sha256:")[:12],
        state=_text(item.get("status") or "?", 40),
        update=update,
        update_to=update_to,
        update_digest=_text(result.get("digest") or "", 80).removeprefix("sha256:")[:12],
        unchecked=unchecked,
        registry=_text(_obj(image.get("registry")).get("name"), 60) or None,
    )


async def read_wud(url: str) -> list[Container]:
    """All containers WUD watches. Raises on any problem; the caller keeps what it knew."""
    async with (
        asyncio.timeout(20),
        httpx.AsyncClient(timeout=10) as client,
        client.stream("GET", url.rstrip("/") + "/api/containers") as response,
    ):
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("WUD's answer is too large")
    items = json.loads(body)
    if not isinstance(items, list):
        raise ValueError("unexpected answer from WUD")
    containers = sorted((from_wud(i) for i in items if isinstance(i, dict)), key=lambda c: c.key.lower())
    # The same key twice (a WUD quirk): keep the first, so nothing flips back and forth.
    unique: dict[str, Container] = {}
    for c in containers:
        unique.setdefault(c.key, c)
    return list(unique.values())


async def from_collector(items: list[Any], registries: Any, wait: float = 0) -> list[Container]:
    """Containers as the collector lists them (host/collector.py), with updates asked of their
    registries (registry.py) instead of WUD. With `wait`, the registries are asked anew and
    their answers awaited for up to that many seconds (a page's "read again"); otherwise what
    they said last is used."""
    if wait:
        checks = [
            registries.recheck(_text(i.get("image"), 300), _digests(i)) for i in items if isinstance(i, dict)
        ]
        if tasks := [t for t in checks if t is not None]:
            await asyncio.wait(tasks, timeout=wait)  # the rest goes on and shows next time
    out: list[Container] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        image_ref = _text(item.get("image"), 300)
        digests = _digests(item)
        ref = parse(image_ref)
        update = registries.peek(image_ref, digests)  # never waits for a registry
        name = _text(item.get("name") or "?", 100)
        out.append(
            Container(
                key=name,
                wud_id="",
                name=name,
                image=ref.name.removeprefix("library/") if ref else image_ref,
                tag=ref.tag if ref else "?",
                image_id=_text(item.get("image_id"), 80).removeprefix("sha256:")[:12],
                state=_text(item.get("state") or "?", 40),
                update=update.update,
                update_to=update.update_to,
                update_digest=update.digest,
                unchecked=update.unchecked,
                registry=(ref.registry.replace("registry-1.docker.io", "docker.io") if ref else None),
                previous=_text(item.get("previous"), 12) or None,
                update_within=update.within,
                compose=_text(item.get("compose"), 300) or None,
                ports=[p for p in item.get("ports") or [] if type(p) is int],
            )
        )
    return sorted(out, key=lambda c: c.key.lower())


def _digests(item: dict[str, Any]) -> list[str]:
    return [d for d in item.get("digests") or [] if isinstance(d, str)][:20]


def as_dicts(containers: list[Container]) -> list[dict[str, Any]]:
    return [asdict(c) for c in containers]


async def run_trigger(url: str, wud_id: str, trigger: str) -> None:
    """Ask WUD to run one of its triggers (e.g. docker.local, which pulls and recreates) on one
    container. Raises when WUD refuses."""
    kind, _, name = trigger.partition(".")
    async with asyncio.timeout(600), httpx.AsyncClient(timeout=600) as client:
        r = await client.post(f"{url.rstrip('/')}/api/containers/{wud_id}/triggers/{kind}/{name}")
        r.raise_for_status()
