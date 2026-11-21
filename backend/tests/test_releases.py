"""Whether a newer Kastellan is out."""

import httpx

from kastellan import releases
from kastellan.releases import Release, newer


def test_only_a_newer_release_counts() -> None:
    r = Release("0.9.0", "https://github.com/andiwand/kastellan/releases/tag/v0.9.0")
    assert newer(r, "0.8.0") is r
    assert newer(r, "0.9.0") is None and newer(r, "0.10.0") is None
    assert newer(r, "0.9.0.dev3") is None  # a development build: no word
    assert newer(None, "0.8.0") is None


async def test_asking_github() -> None:
    answers = {
        "tag_name": "v0.9.0",
        "prerelease": True,
        "html_url": "https://github.com/andiwand/kastellan/releases/tag/v0.9.0",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == releases.URL
        return httpx.Response(200, json=[answers])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await releases.latest(client) == Release("0.9.0", answers["html_url"])
        answers["html_url"] = "https://evil.example/x"  # its link goes nowhere else
        assert await releases.latest(client) is None
        answers.update(tag_name="nightly", html_url="https://github.com/andiwand/kastellan/x")
        assert await releases.latest(client) is None


async def test_highest_published_version_wins() -> None:
    answers = [
        {
            "tag_name": tag,
            "html_url": f"https://github.com/andiwand/kastellan/releases/tag/{tag}",
            "draft": draft,
            "prerelease": True,
        }
        for tag, draft in [("v0.9.0", False), ("v2.0.0", True), ("v0.10.0", False), ("v0.11.0-rc1", False)]
    ]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=answers))
    ) as client:
        assert (await releases.latest(client)).version == "0.10.0"
