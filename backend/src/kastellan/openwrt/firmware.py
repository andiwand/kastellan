"""OpenWrt firmware: whether a device's release still gets fixes, what to move to, and an image
for it from OpenWrt's own image builder (attended sysupgrade, sysupgrade.openwrt.org), with the
packages the device has installed.

Moves go one release series at a time (22.03 -> 23.05 -> 24.10 -> 25.12): OpenWrt keeps settings
across one series; skipping several isn't something it promises. Within a series, the newest
point release first.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

VERSIONS = "https://downloads.openwrt.org/.versions.json"
ASU = "https://sysupgrade.openwrt.org"
MAX_IMAGE = 64 * 1024 * 1024
POINT = re.compile(r"^(\d+\.\d+)\.(\d+)$")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


@dataclass(frozen=True)
class Advice:
    current: str
    series: str
    supported: bool  # still gets fixes (the stable or the previous series)
    newest_in_series: str | None  # a newer point release of the same series
    next_step: str | None  # what to move to now (one step)
    stable: str
    oldstable: str

    def view(self) -> dict[str, Any]:
        return self.__dict__.copy()


def advise(current: str, versions: dict[str, Any]) -> Advice | None:
    """What a device on `current` should do, from OpenWrt's list of releases."""
    m = POINT.match(current or "")
    if not m:
        return None  # a snapshot or an unusual build: no advice
    series = m.group(1)
    releases = [v for v in versions.get("versions_list", []) if POINT.match(v)]
    by_series: dict[str, list[str]] = {}
    for v in releases:
        by_series.setdefault(POINT.match(v).group(1), []).append(v)  # type: ignore[union-attr]
    newest = {s: max(vs, key=_key) for s, vs in by_series.items()}
    order = sorted(newest, key=_key)
    stable, oldstable = str(versions.get("stable_version", "")), str(versions.get("oldstable_version", ""))
    supported_series = {v.rsplit(".", 1)[0] for v in (stable, oldstable) if v}
    in_series = newest.get(series)
    newer_point = in_series if in_series and _key(in_series) > _key(current) else None
    later = [s for s in order if _key(s) > _key(series)]
    next_step = newer_point or (newest[later[0]] if later else None)
    return Advice(current, series, series in supported_series, newer_point, next_step, stable, oldstable)


class Releases:
    """OpenWrt's list of releases, fetched now and then."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.data: dict[str, Any] | None = None
        self.fetched = 0.0
        self.transport = transport

    async def get(self) -> dict[str, Any] | None:
        if self.data is None or time.monotonic() - self.fetched > 12 * 3600:
            try:
                async with httpx.AsyncClient(timeout=15, transport=self.transport) as c:
                    r = await c.get(VERSIONS)
                    r.raise_for_status()
                    data = r.json()
                if isinstance(data, dict) and isinstance(data.get("versions_list"), list):
                    self.data, self.fetched = data, time.monotonic()
            except Exception:
                pass  # keep what we had; no advice is better than a wrong one
        return self.data


# Targets whose images aren't "sysupgrade" ones (combined, combined-efi): which one a machine
# needs depends on how it boots, which Kastellan can't see; those are updated by hand.
COMBINED = ("x86/", "armsr/", "loongarch64/")


async def build_image(
    target: str,
    board_name: str,
    version: str,
    packages: list[str],
    progress=None,
    transport: httpx.AsyncBaseTransport | None = None,
    filesystem: str | None = None,
) -> tuple[bytes, str, str]:
    """Ask OpenWrt's image builder for a sysupgrade image of `version` for this device, with
    the packages installed on purpose (the defaults of the new release come along by
    themselves, as with LuCI's attended sysupgrade); returns (image, its sha256, its name).
    Builds can take a few minutes."""
    if target.startswith(COMBINED):
        raise ValueError(f"{target} images depend on how the machine boots; update it by hand")
    body: dict[str, Any] = {
        "distro": "openwrt",
        "version": version,
        "target": target,
        "profile": board_name.replace(",", "_"),
        "packages": sorted(set(packages))[:300],
        "diff_packages": True,
    }
    if filesystem:
        body["filesystem"] = filesystem
    said = None

    def tell(text: str) -> None:
        nonlocal said
        if progress and text != said:
            said = text
            progress(text)

    async with httpx.AsyncClient(timeout=60, transport=transport, follow_redirects=True) as c:
        r = await c.post(f"{ASU}/api/v1/build", json=body)
        for _ in range(240):  # up to about 20 minutes
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if r.status_code == 200:
                break
            if r.status_code == 529:  # the builder is busy: ask again in a while
                tell("the image builder is busy; waiting")
                await asyncio.sleep(15)
                r = await c.post(f"{ASU}/api/v1/build", json=body)
                continue
            if r.status_code != 202:
                why = data.get("detail") or data.get("status") or r.status_code
                raise ValueError(f"the image builder said: {str(why)[:300]}")
            state = data.get("imagebuilder_status") or data.get("detail") or "queued"
            tell(f"the image builder is at: {state}")
            await asyncio.sleep(5)
            r = await c.get(f"{ASU}/api/v1/build/{data.get('request_hash', '')}")
        else:
            raise TimeoutError("the image builder took too long")
        images = [i for i in data.get("images") or [] if i.get("type") == "sysupgrade"]
        if filesystem:
            images = [i for i in images if i.get("filesystem") in (None, filesystem)]
        if not images:
            raise ValueError("the image builder made no sysupgrade image for this device")
        image = images[0]
        url = f"{ASU}/store/{data.get('bin_dir', '')}/{image['name']}"
        tell(f"downloading {image['name']}")
        async with c.stream("GET", url) as dl:
            dl.raise_for_status()
            blob = bytearray()
            async for chunk in dl.aiter_bytes():
                blob += chunk
                if len(blob) > MAX_IMAGE:
                    raise ValueError("the image is too large")
    sha = hashlib.sha256(blob).hexdigest()
    if sha != image.get("sha256"):
        raise ValueError("the downloaded image doesn't match its checksum")
    return bytes(blob), sha, image["name"]
