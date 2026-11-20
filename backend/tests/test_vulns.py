import json
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
