"""Whether a container's image has an update, asked of its registry directly (Docker Hub,
ghcr.io, lscr.io, quay.io, any registry speaking the v2 API), anonymously.

- A newer build of the same tag: the tag's digest now differs from the digest the container's
  image was pulled as (its RepoDigests, from the collector).
- A newer version: for tags that look like versions (1.2.3, v2.1, 16-alpine), the highest tag
  of the same shape (prefix, number of parts, suffix) that is greater.

No access to the host is needed for this. Answers are kept for hours: registries limit how often
they may be asked (Docker Hub especially), and images don't change by the minute.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

KEPT = 6 * 3600.0
KEPT_FAILED = 1800.0
MAX_BODY = 4 * 1024 * 1024  # a page of tags or a token is far less
MAX_PAGES = 40  # Docker Hub: 1000 tags a page, alphabetical; past this, "can't tell"
CHECK_TIME = 45.0  # one image, all its requests
MAX_CACHE = 2000
ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
    ]
)
VERSION = re.compile(r"^(?P<prefix>v?)(?P<nums>\d+(?:\.\d+){0,3})(?P<suffix>[-_.][A-Za-z][\w.-]*)?$")


@dataclass(frozen=True)
class Ref:
    registry: str  # the API host
    name: str  # library/postgres, linuxserver/plex
    tag: str

    @property
    def text(self) -> str:
        return f"{self.registry}/{self.name}:{self.tag}"


def parse(image: str) -> Ref | None:
    """An image reference as Docker writes it ("postgres:16", "ghcr.io/a/b:1.0"), or None for
    one pinned to a digest (nothing to update) or unreadable."""
    if "@" in image:
        return None
    name, tag = image, "latest"
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = image.rsplit(":", 1)
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        registry, path = name.split("/", 1)
    else:
        registry, path = "registry-1.docker.io", name
        if "/" not in path:
            path = f"library/{path}"
    if registry == "docker.io":
        registry = "registry-1.docker.io"
    if not re.match(r"^[a-z0-9._/-]+$", path) or not re.match(r"^[\w.-]{1,128}$", tag):
        return None
    return Ref(registry, path, tag)


def same_repo(ref: Ref, digest_entry: str) -> bool:
    """Whether a RepoDigests entry ("repo@sha256:...") is of this image."""
    repo = digest_entry.split("@", 1)[0]
    other = parse(repo + ":x")
    return other is not None and (other.registry, other.name) == (ref.registry, ref.name)


@dataclass(frozen=True)
class Update:
    update: bool
    update_to: str | None  # a newer version tag, or "newer build"
    unchecked: str | None  # why it can't be told
    digest: str = ""  # the tag's digest now (short)
    within: str | None = None  # when update_to is a new major version: the newest of this one


def _key(parts: str) -> tuple:
    return tuple(int(x) for x in parts.split("."))


def newer_tag(tag: str, tags: list[str], same_major: bool = False) -> str | None:
    """The greatest tag of the same shape as `tag` and greater, or None. Dates (20250108) are
    not versions: they never count as newer than a version like 3. With same_major, only tags
    whose first number is the same (2.9.4 for 2.8.1, not 3.0.0)."""
    m = VERSION.match(tag)
    if not m:
        return None
    shape = (m["prefix"], m["nums"].count("."), m["suffix"] or "")
    best, best_key = None, _key(m["nums"])
    for t in tags:
        o = VERSION.match(t)
        if not o or (o["prefix"], o["nums"].count("."), o["suffix"] or "") != shape:
            continue
        k = _key(o["nums"])
        if best_key[0] < 1000 <= k[0]:
            continue  # a date or a build number, not the next version
        if same_major and (k[0] != _key(m["nums"])[0] or len(k) < 2):
            continue
        if k > best_key:
            best, best_key = t, k
    return best


def public_host(host: str) -> bool:
    """Whether a registry (or its token service) is on the internet: the hub asks no address on
    a local network for a host's container (that would let a host send it anywhere inside)."""
    import ipaddress
    import socket

    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host.strip("[]")
    try:
        infos = socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addresses = {ipaddress.ip_address(i[4][0]) for i in infos}
    return bool(addresses) and all(a.is_global for a in addresses)


class TooBig(Exception):
    pass


class Registries:
    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        allowed_host: Callable[[str], bool] = public_host,
    ) -> None:
        self._cache: dict[tuple[str, str, str, tuple[str, ...]], tuple[float, Update]] = {}
        self._tokens: dict[tuple[str, str], tuple[float, str]] = {}
        self._limit = asyncio.Semaphore(4)
        self._transport = transport
        self._allowed = allowed_host
        self._running: dict[tuple[str, str, str, tuple[str, ...]], asyncio.Task[Update]] = {}

    def _key(self, ref: Ref, digests: list[str]) -> tuple[str, str, str, tuple[str, ...]]:
        return (ref.registry, ref.name, ref.tag, tuple(sorted(digests)))

    def peek(self, image: str, digests: list[str]) -> Update:
        """What is known now, without waiting: a check that isn't done yet starts in the
        background (the host's polling never waits for a registry)."""
        ref = parse(image)
        if ref is None:
            return Update(False, None, "pinned to a digest" if "@" in image else "can't read the image name")
        key = self._key(ref, digests)
        hit = self._cache.get(key)
        fresh = hit and time.monotonic() - hit[0] < (KEPT if hit[1].unchecked is None else KEPT_FAILED)
        if not fresh and key not in self._running:
            task = asyncio.get_running_loop().create_task(self.check(image, digests))
            self._running[key] = task
            task.add_done_callback(lambda _: self._running.pop(key, None))
        return hit[1] if hit else Update(False, None, "not checked yet")

    async def check(self, image: str, digests: list[str]) -> Update:
        ref = parse(image)
        if ref is None:
            return Update(False, None, "pinned to a digest" if "@" in image else "can't read the image name")
        key = self._key(ref, digests)
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < (KEPT if hit[1].unchecked is None else KEPT_FAILED):
            return hit[1]
        async with self._limit:
            try:
                if not await asyncio.to_thread(self._allowed, ref.registry):
                    result = Update(
                        False, None, f"{ref.registry} is on a local network (or unknown): not asked"
                    )
                else:
                    async with asyncio.timeout(CHECK_TIME):
                        result = await self._check(ref, digests)
            except TooBig:
                result = Update(False, None, f"{ref.registry}: too many tags to tell")
            except Exception as e:  # a registry's trouble is that container's note, nothing more
                result = Update(False, None, f"{ref.registry}: {type(e).__name__}")
        if len(self._cache) >= MAX_CACHE:
            for old in sorted(self._cache, key=lambda k: self._cache[k][0])[: MAX_CACHE // 4]:
                del self._cache[old]
        self._cache[key] = (time.monotonic(), result)
        return result

    async def _check(self, ref: Ref, digests: list[str]) -> Update:
        async with httpx.AsyncClient(timeout=15, transport=self._transport, follow_redirects=False) as client:
            status, headers, _ = await self._get(
                client, ref, f"/manifests/{ref.tag}", method="HEAD", accept=ACCEPT
            )
            if status == 404:
                return Update(False, None, f"the registry has no tag {ref.tag}")
            if status >= 400:
                raise ValueError(f"manifest: {status}")
            remote = headers.get("docker-content-digest", "")
            mine = [d.split("@", 1)[1] for d in digests if "@" in d and same_repo(ref, d)]
            newer = within = None
            if VERSION.match(ref.tag):
                tags = await self._tags(client, ref)
                newer = newer_tag(ref.tag, tags)
                within = newer_tag(ref.tag, tags, same_major=True)
            if newer:
                return Update(True, newer, None, remote[7:19], within if within != newer else None)
            if not mine:
                return Update(
                    False, None, "the image wasn't pulled from this registry (no digest to compare)"
                )
            if not remote:
                return Update(False, None, "the registry gave no digest")
            if remote not in mine:
                return Update(True, "newer build", None, remote[7:19])
            return Update(False, None, None, remote[7:19])

    async def _tags(self, client: httpx.AsyncClient, ref: Ref) -> list[str]:
        tags: list[str] = []
        path = "/tags/list?n=1000"
        for _ in range(MAX_PAGES):
            status, headers, body = await self._get(client, ref, path)
            if status >= 400:
                raise ValueError(f"tags: {status}")
            data = json.loads(body)
            tags += [
                t for t in (data.get("tags") or [] if isinstance(data, dict) else []) if isinstance(t, str)
            ]
            m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("link", ""))
            if not m:
                return tags
            nxt = m.group(1)
            path = nxt.split(f"/v2/{ref.name}", 1)[-1] if f"/v2/{ref.name}" in nxt else ""
            if not path.startswith("/tags/list"):
                return tags
        raise TooBig()  # the newest may be on a page not read: not "up to date"

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: dict[str, str],
        params: dict | None = None,
    ) -> tuple[int, httpx.Headers, bytes]:
        """One request, following at most 3 redirects, each to https on the internet, reading
        at most MAX_BODY bytes."""
        for _ in range(4):
            host = httpx.URL(url).host
            if not url.startswith("https://") or not await asyncio.to_thread(self._allowed, host):
                raise ValueError("a registry pointed elsewhere (not https on the internet)")
            async with client.stream(method, url, headers=headers, params=params) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url, params = str(r.url.join(r.headers["location"])), None
                    continue
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BODY:
                        raise ValueError("the registry's answer is too long")
                return r.status_code, r.headers, bytes(body)
        raise ValueError("too many redirects")

    async def _get(
        self, client: httpx.AsyncClient, ref: Ref, path: str, method: str = "GET", accept: str | None = None
    ) -> tuple[int, httpx.Headers, bytes]:
        url = f"https://{ref.registry}/v2/{ref.name}{path}"
        headers = {"Accept": accept} if accept else {}
        token = self._token_for(ref)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, rh, body = await self._fetch(client, method, url, headers)
        if status == 401 and "bearer" in rh.get("www-authenticate", "").lower():
            token = await self._token(client, ref, rh["www-authenticate"])
            headers["Authorization"] = f"Bearer {token}"
            status, rh, body = await self._fetch(client, method, url, headers)
        return status, rh, body

    def _token_for(self, ref: Ref) -> str | None:
        hit = self._tokens.get((ref.registry, ref.name))
        return hit[1] if hit and time.monotonic() < hit[0] else None

    async def _token(self, client: httpx.AsyncClient, ref: Ref, challenge: str) -> str:
        """An anonymous pull token, as the registry's challenge says where to get one (https on
        the internet only)."""
        fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = fields.get("realm", "")
        params = {"scope": f"repository:{ref.name}:pull"}  # only pulling this image, whatever is asked
        if fields.get("service"):
            params["service"] = fields["service"][:200]
        status, _, body = await self._fetch(client, "GET", realm, {}, params)
        if status >= 400:
            raise ValueError(f"token: {status}")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("token: unreadable")
        token = str(data.get("token") or data.get("access_token") or "")[:8192]
        life = data.get("expires_in")
        life = float(life) if isinstance(life, int | float) and 0 < life < 86400 else 300.0
        self._tokens[(ref.registry, ref.name)] = (time.monotonic() + max(30.0, life - 30), token)
        if len(self._tokens) > MAX_CACHE:
            self._tokens.clear()
        return token
