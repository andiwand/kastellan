import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from kastellan import vulns
from kastellan.vulns import Sources, from_image, from_software, parse_software

NVD = {
    "totalResults": 1,
    "vulnerabilities": [
        {
            "cve": {
                "id": "CVE-2025-26466",
                "published": "2025-02-28T22:15:40.453",
                "descriptions": [{"lang": "en", "value": "A flaw was found in the OpenSSH package."}],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 5.9, "baseSeverity": "MEDIUM"}}]},
            }
        },
        {
            "cve": {
                "id": "CVE-2024-6387",
                "descriptions": [],
                "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 8.1}}]},
            }
        },
    ],
}
OSV = {
    "vulns": [
        {
            "id": "GHSA-5hxg-r395-fqxx",
            "aliases": ["CVE-2026-64825", "PYSEC-2026-3669"],
            "summary": "Home Assistant: something bad",
            "database_specific": {"severity": "CRITICAL"},
            "affected": [{"ranges": [{"events": [{"introduced": "0"}, {"fixed": "2026.11.3"}]}]}],
        }
    ]
}
KEV = {"vulnerabilities": [{"cveID": "CVE-2024-6387"}]}


def test_products() -> None:
    ubuntu = from_software("OpenSSH", "10.2p1", "Ubuntu-2ubuntu3.8")
    assert ubuntu and ubuntu.cpe == "cpe:2.3:a:openbsd:openssh:10.2:p1:*:*:*:*:*:*"
    assert ubuntu.distro == "Ubuntu-2ubuntu3.8"
    plain = from_software("OpenSSH", "9.8")
    assert plain and plain.distro is None
    assert plain.cpe == "cpe:2.3:a:openbsd:openssh:9.8:-:*:*:*:*:*:*"  # not "*": that matched 29 CVEs
    assert from_software("Dropbear", None) is None  # no version: nothing to look up
    assert from_software("Tasmota", "14.4.0") is None  # no known product
    fritz = from_software("FRITZ!OS", "8.25")
    assert fritz and fritz.cpe == "cpe:2.3:o:avm:fritz\\!os:8.25:*:*:*:*:*:*:*"
    ha = from_image("home-assistant/home-assistant", "2026.11.2")
    assert ha and ha.osv == ("PyPI", "homeassistant", "2026.11.2")
    assert from_image("home-assistant/home-assistant", "stable") is None  # not a version
    assert parse_software("OpenSSH 10.2p1 (Ubuntu-2ubuntu3.8)") == ("OpenSSH", "10.2p1", "Ubuntu-2ubuntu3.8")
    assert parse_software("FRITZ!OS 8.25") == ("FRITZ!OS", "8.25", None)
    assert parse_software("Home Assistant") == ("Home Assistant", None, None)


def transport(fail: set[str] | None = None) -> httpx.MockTransport:
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host in (fail or set()):
            return httpx.Response(503)
        if "nvd" in request.url.host:
            assert request.url.params["virtualMatchString"].startswith("cpe:2.3:")
            assert "noRejected" in str(request.url)
            return httpx.Response(200, json=NVD)
        if "osv" in request.url.host:
            assert json.loads(request.content)["package"]["ecosystem"] == "PyPI"
            return httpx.Response(200, json=OSV)
        return httpx.Response(200, json=KEV)

    t = httpx.MockTransport(handle)
    t.calls = calls  # type: ignore[attr-defined]
    return t


@pytest.fixture(autouse=True)
def no_nvd_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vulns.time, "monotonic", lambda: 1e12)  # never wait for NVD's rate limit


async def test_lookup_marks_exploited_and_distro(tmp_path: Path) -> None:
    sources = Sources(tmp_path)
    t = transport()
    async with httpx.AsyncClient(transport=t) as client:
        await sources.refresh_kev(client)
        found = await sources.lookup(client, from_software("OpenSSH", "10.2p1", "Ubuntu-2ubuntu3.8"))
        assert found is not None
        by_id = {f.id: f for f in found}
        assert by_id["CVE-2024-6387"].exploited and by_id["CVE-2024-6387"].severity == "high"
        assert not by_id["CVE-2025-26466"].exploited and by_id["CVE-2025-26466"].severity == "medium"
        assert all(f.confidence == "upstream" for f in found)  # Ubuntu may have patched it

        ha = await sources.lookup(client, from_image("home-assistant/home-assistant", "2026.11.2"))
        assert ha and ha[0].id == "CVE-2026-64825" and ha[0].fixed_in == "2026.11.3"
        assert ha[0].severity == "critical" and ha[0].confidence == "exact"

        calls = len(t.calls)
        await sources.lookup(client, from_software("OpenSSH", "10.2p1", "Ubuntu-2ubuntu3.8"))
        assert len(t.calls) == calls  # cached

    assert Sources(tmp_path).lookup  # the cache survives (read in __init__)


async def test_unreachable_source_is_unknown_not_safe(tmp_path: Path) -> None:
    sources = Sources(tmp_path)
    async with httpx.AsyncClient(transport=transport(fail={"services.nvd.nist.gov"})) as client:
        assert await sources.lookup(client, from_software("OpenSSH", "9.8")) is None
    assert "nvd" in sources.errors


def test_osv_merges_databases_and_scores_vectors() -> None:
    from kastellan.vulns import parse_osv

    data = {
        "vulns": [
            {
                "id": "GHSA-1",
                "aliases": ["CVE-2024-1"],
                "database_specific": {"severity": "HIGH"},
                "affected": [{"ranges": [{"events": [{"fixed": "1.2.0"}, {"fixed": "1.1.9"}]}]}],
            },
            {
                "id": "PYSEC-2024-9",
                "aliases": ["CVE-2024-1"],
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
            },
            {
                "id": "GO-2024-7",
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:H"}],
            },
        ]
    }
    found = {f.id: f for f in parse_osv(data, "1.1.5")}
    assert set(found) == {"CVE-2024-1", "GO-2024-7"}  # one finding per CVE, not one per database
    assert found["CVE-2024-1"].severity == "critical" and found["CVE-2024-1"].score == 9.8
    assert found["CVE-2024-1"].fixed_in == "1.1.9"  # the first fix above 1.1.5
    assert found["GO-2024-7"].severity == "medium"  # from its vector, not "unknown"


def test_moving_tags_are_not_looked_up() -> None:
    assert from_image("library/redis", "7.4") is None  # "before 7.4.6" would wrongly match
    assert from_image("library/redis", "7.4.6") is not None
    assert from_image("library/nginx", "1.27.3-alpine") is not None
    assert from_software("nginx", "1.24.0", "Ubuntu").distro == "Ubuntu"  # any distribution counts
    assert len(from_software("nginx", "1.24.0").more_cpes) == 1  # also NVD's older vendor name


def sshhole(*matches: dict) -> dict:
    """CVE-2026-123158 as NVD lists it: OpenSSH before 10.6, and 10.6 and 10.6p1 (fixed in 10.6p2)."""
    return {
        "vulnerabilities": [
            {
                "cve": {
                    "id": "CVE-2026-123158",
                    "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 8.1}}]},
                    "configurations": [{"nodes": [{"operator": "OR", "cpeMatch": list(matches)}]}],
                }
            }
        ]
    }


OPENSSH = "cpe:2.3:a:openbsd:openssh"
RANGE = {"vulnerable": True, "criteria": f"{OPENSSH}:*:*:*:*:*:*:*:*", "versionEndExcluding": "10.6"}
PLAIN = {"vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:10.6:-:*:*:*:*:*:*"}
P1 = {"vulnerable": True, "criteria": "cpe:2.3:a:openbsd:openssh:10.6:p1:*:*:*:*:*:*"}
ALPINE = {"vulnerable": False, "criteria": "cpe:2.3:o:alpinelinux:alpine_linux:*:*:*:*:*:*:*:*"}


def cpe_of(version: str) -> str:
    product = from_software("OpenSSH", version)
    assert product and product.cpe
    return product.cpe


def test_a_banner_without_patch_level_is_not_a_firm_finding() -> None:
    """OpenWrt's OpenSSH 10.6p2 says "OpenSSH_10.6": NVD's 10.6 entry matches, but p2 is fixed."""
    data = sshhole(RANGE, PLAIN, P1, ALPINE)
    assert vulns.parse_nvd(data, cpe_of("10.6"))[0].patch_level  # 10.6 or 10.6p2: can't tell
    assert not vulns.parse_nvd(data, cpe_of("10.6p1"))[0].patch_level  # said, and affected
    assert not vulns.parse_nvd(data, cpe_of("9.8"))[0].patch_level  # every 9.8 is in the range
    assert not vulns.parse_nvd(data)[0].patch_level  # nothing asked, nothing assumed
    # NVD naming only "10.6" (no patch levels told apart) or a range over the whole 10.6 line.
    assert not vulns.parse_nvd(sshhole(PLAIN), cpe_of("10.6"))[0].patch_level
    whole = {**RANGE, "versionEndExcluding": "10.7"}
    assert not vulns.parse_nvd(sshhole(whole, PLAIN, P1), cpe_of("10.6"))[0].patch_level
    # A range ending at a patch level of the version itself leaves the later ones out.
    upto_p2 = {**RANGE, "versionStartIncluding": "10.6", "versionEndExcluding": "10.6p2"}
    assert vulns.parse_nvd(sshhole(upto_p2, PLAIN, P1), cpe_of("10.6"))[0].patch_level


async def test_lookup_says_check_the_device_for_an_unknown_patch_level(tmp_path: Path) -> None:
    from kastellan.config import Device
    from kastellan.vuln_policy import assess

    def nvd(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"totalResults": 1, "resultsPerPage": 1, **sshhole(RANGE, PLAIN, P1)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(nvd)) as client:
        sources = Sources(tmp_path)
        [plain] = await sources.lookup(client, from_software("OpenSSH", "10.6")) or [None]
        sources._nvd_next = 0  # time stands still here (no_nvd_wait): no rate-limit pause
        [said] = await sources.lookup(client, from_software("OpenSSH", "10.6p1")) or [None]
    assert plain.confidence == "patch" and said.confidence == "exact"
    exposed = Device(host="wifi1", exposed=True)
    assert assess(exposed, asdict(plain)).alarm is None  # no alarm, shown as "check the device"
    assert assess(exposed, asdict(said)).alarm is not None
