"""Whether a newer Kastellan is out: GitHub's releases of the project, asked once a day
(`check_releases: false` in config.yaml turns it off; demo mode never asks). Only the project's
public release list is fetched: no inventory or credentials are sent."""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

URL = "https://api.github.com/repos/andiwand/kastellan/releases?per_page=100"
EVERY = 24 * 3600


@dataclass
class Release:
    version: str  # "0.9.0"
    url: str  # its page: what changed


def version_tuple(text: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: Release | None, running: str) -> Release | None:
    """The latest release if it is newer than what runs (a development build is never older)."""
    if latest is None:
        return None
    a, b = version_tuple(latest.version), version_tuple(running)
    return latest if a and b and a > b else None


async def latest(client: httpx.AsyncClient) -> Release | None:
    r = await client.get(URL, headers={"Accept": "application/vnd.github+json"}, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return None
    found = []
    for item in data:
        if not isinstance(item, dict) or item.get("draft"):
            continue
        tag, page = str(item.get("tag_name") or ""), str(item.get("html_url") or "")
        version = version_tuple(tag)
        if version and page == f"https://github.com/andiwand/kastellan/releases/tag/{tag}":
            found.append((version, Release(tag.removeprefix("v"), page)))
    # The project's 0.x releases are published with GitHub's prerelease flag.
    return max(found, key=lambda item: item[0])[1] if found else None
