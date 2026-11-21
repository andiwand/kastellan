"""Known vulnerabilities in what the network runs, from several sources.

What is looked up comes from what Kastellan already knows: the software services report (SSH
and web servers, from banners), firmware from drivers (FRITZ!OS), and container images with a
known product mapping. Each becomes a `Product`, and each source answers in its own terms:

- NVD (nvd.nist.gov, CVE API 2.0): by CPE name and version; matches version ranges.
- OSV (osv.dev): by package ecosystem, name and exact version (PyPI, Go, npm, ...).
- CISA KEV: the catalogue of vulnerabilities known to be exploited; it marks findings, it
  finds none by itself.

Honesty rules:
- When the software doesn't say its patch level ("OpenSSH_10.6", where 10.6p2 may be meant) and
  NVD names only some patch levels of exactly that version as affected (10.6 and 10.6p1, no
  range over the whole 10.6 line), a later patch level may have fixed it: `confidence="patch"`,
  "check the device", never an alarm or an automatic action.
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
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from . import cvss, software

log = logging.getLogger(__name__)

NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
OSV_URL = "https://api.osv.dev/v1/query"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
CACHE_SECONDS = 24 * 3600
MAX_BYTES = 20 * 1024 * 1024
DISTROS = tuple(d.lower() for d in software.DISTROS)


@dataclass(frozen=True)
class Product:
    """Something to look up: a name for people, and what each source needs."""

    label: str  # "OpenSSH 10.2p1"
    cpe: str | None = None  # cpe:2.3:a:openbsd:openssh:10.2:p1:*:*:*:*:*:*
    more_cpes: tuple[str, ...] = ()  # the same product under older vendor names
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
    confidence: str = "exact"  # exact; upstream (a distribution may have fixed it); patch (see top)
    # NVD lists only some patch levels of the version that was asked without one (see top).
    patch_level: bool = False


# --- What a piece of software is, in the sources' terms -------------------------------------

# Software name (as software.py reports it) → CPE vendor:product; several when NVD filed the
# product under other names over the years (all are asked). Extend as devices show up.
CPES: dict[str, str | tuple[str, ...]] = {
    "openssh": "openbsd:openssh",
    "dropbear": ("dropbear_ssh_project:dropbear_ssh", "matt_johnston:dropbear_ssh_server"),
    "nginx": ("f5:nginx", "nginx:nginx"),
    "apache": "apache:http_server",
    "lighttpd": "lighttpd:lighttpd",
    "exim": "exim:exim",
    "postfix": "postfix:postfix",
    "vsftpd": ("vsftpd_project:vsftpd", "beasts:vsftpd"),
    "proftpd": "proftpd:proftpd",
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
    names = CPES[key] if isinstance(CPES[key], tuple) else (CPES[key],)
    cpes = [c for c in (_cpe(n, version) for n in names) if c]
    if not cpes:
        return None
    # Any distribution named: it may have patched the bug without changing the upstream version.
    distro = extra if extra and any(d in extra.lower() for d in DISTROS) else None
    return Product(f"{name} {version}", cpe=cpes[0], more_cpes=tuple(cpes[1:]), distro=distro)


EXACT_TAG = re.compile(r"^v?(\d+\.\d+\.\d+)(?:-[a-z][\w.]*)?$")  # 1.27.3, v2.14.1, 1.27.3-alpine


def _image(image: str) -> tuple[str, str] | None:
    """Docker Hub's official images come as "nginx" or "library/nginx"."""
    return IMAGES.get(image) or IMAGES.get(f"library/{image}")


def image_known(image: str) -> bool:
    return _image(image) is not None


def from_image(image: str, tag: str) -> Product | None:
    """A container image with an exact version tag (x.y.z, maybe with a suffix like -alpine), if a
    source knows the product. Moving tags (7.4, 16, latest) are not looked up: a range like
    "before 7.4.6" would match 7.4 although it runs the newest 7.4.x."""
    known = _image(image)
    m = EXACT_TAG.match(tag)
    if known is None or not m:
        return None
    kind, target = known
    version = m.group(1)
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


def _base(version: str) -> tuple[int, ...] | None:
    m = VERSION.match(version)
    return version_key(m.group(1)) if m else None


def _covers(match: dict[str, Any], number: tuple[int, ...]) -> bool:
    """Whether a range entry ("*" with start/end) covers every patch level of `number`. An end at
    a patch level of the version itself (excluding 10.6p2) leaves the later ones out."""
    start_in, start_ex = match.get("versionStartIncluding"), match.get("versionStartExcluding")
    end_in, end_ex = match.get("versionEndIncluding"), match.get("versionEndExcluding")
    for value, ok in (
        (start_in, lambda b: b <= number),
        (start_ex, lambda b: b < number),
        (end_in, lambda b: number <= b),
        (end_ex, lambda b: number < b),
    ):
        if value is None:
            continue
        b = _base(str(value))
        if b is None or not ok(b):
            return False
    return True


def _patch_level_only(cve: dict[str, Any], asked: str | None) -> bool:
    """True when `asked` (a CPE without a patch level: ...:openssh:10.6:-:...) matched this CVE
    only through entries for some patch levels of exactly that version (10.6:-, 10.6:p1), not
    through a range over the whole line: a later patch level (10.6p2) may be fixed."""
    parts = (asked or "").split(":")
    if len(parts) < 7 or parts[6] != "-":
        return False
    product, number = parts[3:5], _base(parts[5])
    if number is None:
        return False
    updates: set[str] = set()
    for config in cve.get("configurations") or []:
        for node in (config or {}).get("nodes") or []:
            for m in (node or {}).get("cpeMatch") or []:
                c = str((m or {}).get("criteria") or "").split(":")
                if not m.get("vulnerable") or len(c) < 7 or c[3:5] != product:
                    continue
                if c[5] == "*":
                    if _covers(m, number):
                        return False  # the whole line, whatever the patch level
                elif c[5] == parts[5]:
                    updates.add(c[6])
    # Only named patch levels, and more than "none": the version has them, NVD tells them apart.
    return bool(updates) and "*" not in updates and bool(updates - {"-"})


def parse_nvd(data: dict[str, Any], asked: str | None = None) -> list[Finding]:
    """`asked`: the CPE the question was for (decides `patch_level`)."""
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
                patch_level=_patch_level_only(cve, asked),
            )
        )
    return out


def version_key(version: str) -> tuple[int, ...]:
    """Enough to order release numbers: 2026.11.3 > 2026.11.2, 10.10.7 > 10.9.0."""
    return tuple(int(n) for n in re.findall(r"\d+", version)[:6])


SEVERITY_ORDER = ["unknown", "low", "medium", "high", "critical"]


def parse_osv(data: dict[str, Any], version: str = "") -> list[Finding]:
    """OSV answers, one finding per CVE (OSV lists the same bug once per database: GHSA, PYSEC,
    GO; they are merged, keeping the most severe). Severity from the entry's label, else from its
    CVSS vector; `fixed_in` is the first fix above the running version."""
    merged: dict[str, Finding] = {}
    for v in data.get("vulns") or []:
        v = v if isinstance(v, dict) else {}
        aliases = [str(a) for a in v.get("aliases") or []][:10]
        vid = str(v.get("id") or "?")
        cve = next((a for a in [vid, *aliases] if a.startswith("CVE-")), None)
        level = str((v.get("database_specific") or {}).get("severity") or "").lower()
        severity = {"moderate": "medium"}.get(level, level)
        score = None
        for entry in v.get("severity") or []:
            if isinstance(entry, dict) and str(entry.get("type", "")).startswith("CVSS_V3"):
                score = cvss.base_score(str(entry.get("score") or ""))
                if score is not None:
                    break
        if severity not in SEVERITY_ORDER[1:]:
            severity = _severity(score)
        fixes = [
            str(event["fixed"])[:40]
            for affected in v.get("affected") or []
            for r in (affected or {}).get("ranges") or []
            if (r or {}).get("type") != "GIT"  # fixed in a commit, not a version
            for event in r.get("events") or []
            if isinstance(event, dict) and event.get("fixed")
        ]
        above = sorted(
            (f for f in fixes if not version or version_key(f) > version_key(version)), key=version_key
        )
        finding = Finding(
            id=cve or vid,
            source="osv",
            summary=str(v.get("summary") or v.get("details") or "")[:400],
            score=score,
            severity=severity,
            published=str(v.get("published") or "")[:10] or None,
            url=f"https://osv.dev/vulnerability/{vid}",
            aliases=[a for a in [vid, *aliases] if a != (cve or vid)],
            fixed_in=above[0] if above else None,
        )
        known = merged.get(finding.id)
        if known is None:
            merged[finding.id] = finding
            continue
        known.aliases = sorted({*known.aliases, *finding.aliases})[:10]
        if SEVERITY_ORDER.index(finding.severity) > SEVERITY_ORDER.index(known.severity):
            known.severity, known.score = finding.severity, finding.score
        known.fixed_in = known.fixed_in or finding.fixed_in
    return list(merged.values())


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
        self._dirty = False
        self.kev_error: str | None = None
        try:
            self._cache = json.loads((folder / "cache.json").read_text())
            kev = json.loads((folder / "kev.json").read_text())
            self._kev, self._kev_fetched = set(kev["ids"]), float(kev["fetched"])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def flush(self) -> None:
        """Write the cache once per sweep; entries older than a week go."""
        if not self._dirty:
            return
        cutoff = time.time() - 7 * 86400
        self._cache = {k: v for k, v in self._cache.items() if v.get("fetched", 0) >= cutoff}
        self._save()
        self._dirty = False

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
            self.kev_error = None
            self._save()
        except (httpx.HTTPError, ValueError, TimeoutError) as e:
            # The copy on disk is used; the app says the "exploited" marks may be out of date.
            self.kev_error = f"CISA KEV: {e or type(e).__name__}"

    async def lookup(self, client: httpx.AsyncClient, product: Product) -> list[Finding] | None:
        """Findings for a product; None when no source could be asked (not "none found")."""
        key = product.cpe or "|".join(product.osv or ())
        cached = self._cache.get(key)
        # Entries cached before patch_level was known are asked again.
        current = cached and all("patch_level" in f for f in cached["findings"])
        if cached and current and time.time() - cached["fetched"] < CACHE_SECONDS:
            findings = [Finding(**f) for f in cached["findings"]]
        else:
            findings = await (
                self._nvd(client, [product.cpe, *product.more_cpes])
                if product.cpe
                else self._osv(client, product.osv)
            )
            if findings is not None:
                self._cache[key] = {"fetched": time.time(), "findings": [asdict(f) for f in findings]}
                self._dirty = True
            elif cached:  # a day old or more, but better than nothing; still marked below
                findings = [Finding(**f) for f in cached["findings"]]
            else:
                return None
        for f in findings:
            f.exploited = f.id in self._kev or any(a in self._kev for a in f.aliases)
            if product.distro and f.source == "nvd":
                f.confidence = "upstream"
            elif f.patch_level and f.source == "nvd":
                f.confidence = "patch"
        return findings

    async def _nvd(self, client: httpx.AsyncClient, cpes: list[str]) -> list[Finding] | None:
        """All pages for all names of the product; None if any page is missing (a partial list
        would look like fewer vulnerabilities than there are)."""
        found: dict[str, Finding] = {}
        for cpe in cpes:
            start = 0
            while True:
                data = await self._nvd_page(client, cpe, start)
                if data is None:
                    return None
                for f in parse_nvd(data, cpe):
                    found.setdefault(f.id, f)
                total, per_page = int(data.get("totalResults") or 0), int(data.get("resultsPerPage") or 0)
                start += per_page
                if per_page <= 0 or start >= total:
                    break
        return list(found.values())

    async def _nvd_page(self, client: httpx.AsyncClient, cpe: str, start: int) -> dict[str, Any] | None:
        async with self._nvd_lock:  # NVD: 5 requests per 30 s without a key, 50 with one
            wait = self._nvd_next - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._nvd_next = time.monotonic() + (0.7 if self.nvd_api_key else 6.5)
            headers = {"apiKey": self.nvd_api_key} if self.nvd_api_key else {}
            params: dict[str, Any] = {"virtualMatchString": cpe, "resultsPerPage": 2000, "startIndex": start}
            try:
                # noRejected is a flag without a value; httpx can't mix that with params, so the
                # query is built here.
                url = f"{NVD_URL}?noRejected&{urlencode(params)}"
                data = await _get_json(client, "GET", url, headers=headers)
                self.errors.pop("nvd", None)
                return data if isinstance(data, dict) else None
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
            return parse_osv(data, version)
        except (httpx.HTTPError, ValueError, TimeoutError) as e:
            self.errors["osv"] = f"OSV: {e or type(e).__name__}"
            return None
