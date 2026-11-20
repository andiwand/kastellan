"""Known vulnerabilities in what the network runs, from several sources.

What is looked up comes from what Kastellan already knows: the software services report (SSH
and web servers, from banners), firmware from drivers (FRITZ!OS), and container images with a
known product mapping. Each becomes a `Product`, and each source answers in its own terms:

- NVD (nvd.nist.gov, CVE API 2.0): by CPE name and version; matches version ranges.
- OSV (osv.dev): by package ecosystem, name and exact version (PyPI, Go, npm, ...).
- CISA KEV: the catalogue of vulnerabilities known to be exploited; it marks findings, it
  finds none by itself.

Honesty rules:
- When the software carries a distribution's package revision ("OpenSSH 10.2p1
  (Ubuntu-2ubuntu3.8)"), the distribution may have patched the bug without changing the upstream
  version. Such NVD matches are `confidence="upstream"`: shown as "check your distribution", never
  a reason for an automatic action.
- A source that can't be reached is reported as such; no answer is not "no vulnerabilities".

Results are cached in <data>/vulns/ for a day; NVD is asked at most once per 6 seconds (its limit
without an API key; with `vulns: {nvd_api_key: ...}` faster).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
OSV_URL = "https://api.osv.dev/v1/query"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CACHE_SECONDS = 24 * 3600
MAX_BYTES = 20 * 1024 * 1024
DISTROS = ("ubuntu", "debian", "raspbian", "alpine", "fedora", "rhel", "centos", "suse")


@dataclass(frozen=True)
class Product:
    """Something to look up: a name for people, and what each source needs."""

    label: str  # "OpenSSH 10.2p1"
    cpe: str | None = None  # cpe:2.3:a:openbsd:openssh:10.2:p1:*:*:*:*:*:*
    osv: tuple[str, str, str] | None = None  # (ecosystem, package, version)
    distro: str | None = None  # a distribution's package revision, if the software said one


@dataclass
class Finding:
    id: str  # CVE-..., or GHSA-... when there is no CVE
    source: str  # nvd, osv
    summary: str
    score: float | None  # CVSS base score
    severity: str  # critical, high, medium, low, unknown
    published: str | None
    url: str
    aliases: list[str] = field(default_factory=list)
    fixed_in: str | None = None
    exploited: bool = False  # in CISA's KEV catalogue
    confidence: str = "exact"  # exact, or upstream (a distribution may have fixed it)


# --- What a piece of software is, in the sources' terms -------------------------------------

# Software name (as software.py reports it) → CPE vendor:product. Extend as devices show up.
CPES = {
    "openssh": "openbsd:openssh",
    "dropbear": "dropbear_ssh_project:dropbear_ssh",
    "nginx": "f5:nginx",
    "apache": "apache:http_server",
    "lighttpd": "lighttpd:lighttpd",
    "exim": "exim:exim",
    "postfix": "postfix:postfix",
    "vsftpd": "vsftpd_project:vsftpd",
    "proftpd": "proftpd:proftpd",
    "fritz!os": "avm:fritz\\!os",
}

# Container image → (ecosystem, package) for OSV, or a CPE vendor:product for NVD; the tag is
# the version when it is one.
IMAGES: dict[str, tuple[str, str]] = {
    "home-assistant/home-assistant": ("osv:PyPI", "homeassistant"),
    "homeassistant/home-assistant": ("osv:PyPI", "homeassistant"),
    "library/nginx": ("cpe", "f5:nginx"),
    "library/postgres": ("cpe", "postgresql:postgresql"),
    "library/redis": ("cpe", "redis:redis"),
    "grafana/grafana": ("osv:Go", "github.com/grafana/grafana"),
    "jellyfin/jellyfin": ("cpe", "jellyfin:jellyfin"),
    "louislam/uptime-kuma": ("osv:npm", "uptime-kuma"),
}

VERSION = re.compile(r"^v?(\d+(?:\.\d+)+)([a-z]\w*)?$", re.I)  # 10.2p1 → 10.2 + p1


def _cpe(vendor_product: str, version: str) -> str | None:
    m = VERSION.match(version)
    if not m:
        return None
    # No patch level given: "-" (none), never "*". With "*", NVD matches every update of the
    # version, and "9.8" sorts below fixes like "9.8p1": OpenSSH 9.8 showed 29 CVEs (regreSSHion
    # marked exploited) instead of its one.
    number, update = m.group(1), m.group(2) or "-"
    return f"cpe:2.3:a:{vendor_product}:{number}:{update}:*:*:*:*:*:*"


def from_software(name: str, version: str | None, extra: str | None = None) -> Product | None:
    """A service's or a firmware's software, if a source knows it."""
    key = name.lower()
    if key == "fritz!os" and version:
        cpe = f"cpe:2.3:o:avm:fritz\\!os:{version}:*:*:*:*:*:*:*"
        return Product(f"FRITZ!OS {version}", cpe=cpe)
    if key not in CPES or not version:
        return None
    cpe = _cpe(CPES[key], version)
    if cpe is None:
        return None
    distro = extra if extra and any(d in extra.lower() for d in DISTROS) and "-" in extra else None
    return Product(f"{name} {version}", cpe=cpe, distro=distro)


def from_image(image: str, tag: str) -> Product | None:
    """A container image with a version tag, if a source knows the product."""
    known = IMAGES.get(image) or IMAGES.get(image.removeprefix("library/"))
    if known is None or not VERSION.match(tag):
        return None
    kind, target = known
    version = tag.lstrip("v")
    if kind == "cpe":
        cpe = _cpe(target, version)
        return Product(f"{image}:{tag}", cpe=cpe) if cpe else None
    return Product(f"{image}:{tag}", osv=(kind.split(":", 1)[1], target, version))


SOFTWARE = re.compile(r"^(?P<name>.+?) (?P<version>v?\d[\w.+~-]*)(?: \((?P<extra>[^)]*)\))?$")


def parse_software(text: str) -> tuple[str, str | None, str | None]:
    """Back from software.py's display form: "OpenSSH 10.2p1 (Ubuntu-2ubuntu3.8)"."""
    m = SOFTWARE.match(text)
    return (m["name"], m["version"], m["extra"]) if m else (text, None, None)


# --- Sources -------------------------------------------------------------------------------------


def _severity(score: float | None) -> str:
    if score is None:
        return "unknown"
    return "critical" if score >= 9 else "high" if score >= 7 else "medium" if score >= 4 else "low"


def parse_nvd(data: dict[str, Any]) -> list[Finding]:
    out = []
    for item in data.get("vulnerabilities") or []:
        cve = (item or {}).get("cve") or {}
        cid = str(cve.get("id") or "")
        if not cid.startswith("CVE-"):
            continue
        metrics = cve.get("metrics") or {}
        score = None
        for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            for m in metrics.get(key) or []:
                value = ((m or {}).get("cvssData") or {}).get("baseScore")
                if isinstance(value, int | float):
                    score = float(value)
                    break
            if score is not None:
                break
        summary = next(
            (d.get("value", "") for d in cve.get("descriptions") or [] if (d or {}).get("lang") == "en"), ""
        )
        out.append(
            Finding(
                id=cid,
                source="nvd",
                summary=str(summary)[:400],
                score=score,
                severity=_severity(score),
                published=str(cve.get("published") or "")[:10] or None,
                url=f"https://nvd.nist.gov/vuln/detail/{cid}",
            )
        )
    return out


def parse_osv(data: dict[str, Any]) -> list[Finding]:
    out = []
    for v in data.get("vulns") or []:
        v = v or {}
        aliases = [str(a) for a in v.get("aliases") or []][:10]
        cve = next((a for a in aliases if a.startswith("CVE-")), None)
        level = str((v.get("database_specific") or {}).get("severity") or "").lower()
        severity = {"moderate": "medium"}.get(level, level) or "unknown"
        fixed = None
        for affected in v.get("affected") or []:
            for r in (affected or {}).get("ranges") or []:
                for event in (r or {}).get("events") or []:
                    if (event or {}).get("fixed"):
                        fixed = str(event["fixed"])[:40]
        vid = str(v.get("id") or "?")
        out.append(
            Finding(
                id=cve or vid,
                source="osv",
                summary=str(v.get("summary") or v.get("details") or "")[:400],
                score=None,
                severity=severity if severity in ("critical", "high", "medium", "low") else "unknown",
                published=str(v.get("published") or "")[:10] or None,
                url=f"https://osv.dev/vulnerability/{vid}",
                aliases=[a for a in [vid, *aliases] if a != (cve or vid)],
                fixed_in=fixed,
            )
        )
    return out


async def _get_json(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
    async with asyncio.timeout(60), client.stream(method, url, **kwargs) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body += chunk
            if len(body) > MAX_BYTES:
                raise ValueError("answer too large")
    return json.loads(body)


class Sources:
    """Asks NVD and OSV, knows KEV, caches everything in <data>/vulns/."""

    def __init__(self, folder: Path, nvd_api_key: str | None = None) -> None:
        self.folder = folder
        self.nvd_api_key = nvd_api_key
        self._cache: dict[str, dict[str, Any]] = {}
        self._kev: set[str] = set()
        self._kev_fetched = 0.0
        self._nvd_next = 0.0
        self._nvd_lock = asyncio.Lock()
        self.errors: dict[str, str] = {}  # source → last problem, for the app
        try:
            self._cache = json.loads((folder / "cache.json").read_text())
            kev = json.loads((folder / "kev.json").read_text())
            self._kev, self._kev_fetched = set(kev["ids"]), float(kev["fetched"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save(self) -> None:
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "cache.json").write_text(json.dumps(self._cache))
            (self.folder / "kev.json").write_text(
                json.dumps({"ids": sorted(self._kev), "fetched": self._kev_fetched})
            )
        except OSError as e:
            log.warning("cannot save the vulnerability cache: %s", e)

    async def refresh_kev(self, client: httpx.AsyncClient) -> None:
        if time.time() - self._kev_fetched < CACHE_SECONDS:
            return
        try:
            data = await _get_json(client, "GET", KEV_URL)
            self._kev = {
                str(v.get("cveID")) for v in data.get("vulnerabilities") or [] if isinstance(v, dict)
            }
            self._kev_fetched = time.time()
            self.errors.pop("kev", None)
            self._save()
        except (httpx.HTTPError, ValueError, TimeoutError) as e:
            self.errors["kev"] = f"CISA KEV: {e or type(e).__name__}"

    async def lookup(self, client: httpx.AsyncClient, product: Product) -> list[Finding] | None:
        """Findings for a product; None when no source could be asked (not "none found")."""
        key = product.cpe or "|".join(product.osv or ())
        cached = self._cache.get(key)
        if cached and time.time() - cached["fetched"] < CACHE_SECONDS:
            findings = [Finding(**f) for f in cached["findings"]]
        else:
            findings = await (
                self._nvd(client, product.cpe) if product.cpe else self._osv(client, product.osv)
            )
            if findings is None:
                return [Finding(**f) for f in cached["findings"]] if cached else None
            self._cache[key] = {"fetched": time.time(), "findings": [asdict(f) for f in findings]}
            self._save()
        for f in findings:
            f.exploited = f.id in self._kev or any(a in self._kev for a in f.aliases)
            if product.distro and f.source == "nvd":
                f.confidence = "upstream"
        return findings

    async def _nvd(self, client: httpx.AsyncClient, cpe: str | None) -> list[Finding] | None:
        async with self._nvd_lock:  # NVD: 5 requests per 30 s without a key, 50 with one
            wait = self._nvd_next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._nvd_next = time.monotonic() + (0.7 if self.nvd_api_key else 6.5)
            headers = {"apiKey": self.nvd_api_key} if self.nvd_api_key else {}
            try:
                data = await _get_json(
                    client,
                    "GET",
                    NVD_URL,
                    params={"virtualMatchString": cpe, "resultsPerPage": 200},
                    headers=headers,
                )
                self.errors.pop("nvd", None)
                return parse_nvd(data)
            except (httpx.HTTPError, ValueError, TimeoutError) as e:
                self.errors["nvd"] = f"NVD: {e or type(e).__name__}"
                return None

    async def _osv(self, client: httpx.AsyncClient, osv: tuple[str, str, str] | None) -> list[Finding] | None:
        assert osv is not None
        ecosystem, package, version = osv
        try:
            data = await _get_json(
                client,
                "POST",
                OSV_URL,
                json={"package": {"name": package, "ecosystem": ecosystem}, "version": version},
            )
            self.errors.pop("osv", None)
            return parse_osv(data)
        except (httpx.HTTPError, ValueError, TimeoutError) as e:
            self.errors["osv"] = f"OSV: {e or type(e).__name__}"
            return None


def worst(findings: list[Finding]) -> str:
    order = ["unknown", "low", "medium", "high", "critical"]
    return max((f.severity for f in findings), key=order.index, default="unknown")


def checked_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
