from __future__ import annotations

import time
from pathlib import Path

import pytest

from kastellan import internet, wifi
from kastellan.internet import Internet, Probe, Sample


@pytest.fixture
def pings(monkeypatch: pytest.MonkeyPatch):
    """Which addresses answer, and how fast."""
    answers: dict[str, list[float]] = {}

    async def fake(address: str, count: int = 4):
        rtts = answers.get(address, [])
        return count, min(count, len(rtts)), rtts[:count]

    async def resolve(name: str):
        return 8.0 if answers.get("dns") else None

    monkeypatch.setattr(internet, "_pings", fake)
    monkeypatch.setattr(internet, "_resolve", resolve)
    return answers


def probe(line: bool | None = True) -> Probe:
    return Probe("192.168.178.1", line, None, ["1.1.1.1", "9.9.9.9"], [], ["example.com"])


async def test_where_an_outage_is(pings) -> None:
    pings |= {"192.168.178.1": [0.5] * 3, "1.1.1.1": [10, 12, 11, 10], "9.9.9.9": [14] * 4, "dns": [1]}
    s = await internet.look(probe())
    assert (s.state, s.step, s.loss, s.ms) == ("up", None, 0.0, 13.0)
    assert (await internet.look(probe(line=False))).state == "up"  # outside replies override stale line data
    pings["9.9.9.9"] = []  # one provider's trouble isn't an outage: half lost, degraded
    s = await internet.look(probe())
    assert (s.state, s.loss) == ("degraded", 0.5)
    pings["1.1.1.1"] = []
    assert (await internet.look(probe())).step == "internet"
    assert (await internet.look(probe(line=False))).step == "line"
    pings["192.168.178.1"] = []
    assert (await internet.look(probe())).step == "router"
    pings |= {"192.168.178.1": [0.5] * 3, "1.1.1.1": [10] * 4, "dns": []}
    assert (await internet.look(probe())).step == "dns"


def test_outages_days_and_events(tmp_path: Path) -> None:
    net = Internet(tmp_path)
    t = time.time() - 3600
    line = {"address": "203.0.113.1", "connected_for": 1000}
    for i in range(10):
        state = "down" if 3 <= i < 6 else "up"
        happened = net.note(
            Sample(t + 30 * i, state, "internet" if state == "down" else None, ms=10), line, 30
        )
        if i == 3:
            assert happened[0]["kind"] == "outage"
        if i == 6:
            assert happened[0]["kind"] == "back"
    (outage,) = net.outages()
    assert outage["seconds"] == 90 and outage["step"] == "internet"
    net.note(Sample(t + 400, "up", ms=10), {"address": "203.0.113.9", "connected_for": 5}, 30)
    kinds = [e["kind"] for e in net.events()]
    assert kinds[:2] == ["address", "reconnect"] or kinds[:2] == ["reconnect", "address"]
    (day,) = net.days()
    assert day["down_seconds"] == 90 and 0 < day["availability"] < 1
    again = Internet(tmp_path)  # it all survives a restart
    assert again.outages()[0]["seconds"] == 90 and again.state.last_address == "203.0.113.9"
    series = net.series(1, 12)
    assert any(b["state"] == "down" for b in series)


def test_slow_speed() -> None:
    now = time.time()
    tests = [{"t": now - i * 3600, "down": 40e6} for i in range(3)]
    assert internet.slow(tests, 250, now)
    assert not internet.slow(tests, 50, now)
    assert not internet.slow(tests[:2], 250, now)
    assert not internet.slow(tests, None, now)


def radio(device: str, band: str, channel: int, width: int = 20, **kw) -> wifi.Radio:
    kw.setdefault("allowed", [1, 6, 11] if band == "2g" else [36, 40, 44, 48, 149, 153, 157, 161])
    return wifi.Radio(device, "radio0" if band == "2g" else "radio1", band, channel, width, **kw)


def test_plan_moves_our_radios_apart_and_off_busy_channels() -> None:
    neighbours = [
        {"channel": 6, "width": 20, "signal": -50},
        {"channel": 6, "width": 20, "signal": -55},
        {"channel": 1, "width": 20, "signal": -85},
    ]
    a = radio("living", "2g", 6, neighbours=neighbours)
    b = radio("attic", "2g", 6, neighbours=neighbours)
    a.hears[b.key] = -58
    result = wifi.plan([a, b])
    chosen = {r["device"]: r["channel"] for r in result["radios"]}
    assert chosen["living"] != chosen["attic"]
    assert 6 not in chosen.values()  # away from the two loud neighbours
    assert result["after"] < result["before"]
    assert all(r["reason"] for r in result["radios"])


def test_plan_keeps_what_is_fine_and_respects_dfs() -> None:
    quiet = radio("living", "2g", 11)
    (r,) = wifi.plan([quiet])["radios"]
    assert r["channel"] == 11 and not r["change"]
    five = radio(
        "living",
        "5g",
        36,
        80,
        neighbours=[
            {"channel": 36, "width": 80, "signal": -45},
            {"channel": 149, "width": 80, "signal": -40},
        ],
    )
    five.allowed = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112]
    (r,) = wifi.plan([five])["radios"]
    assert r["channel"] == 36  # the only block without DFS it may use
    (r,) = wifi.plan([five], dfs=True)["radios"]
    assert r["channel"] in (52, 100)
    wide = radio("living", "2g", 1, 40)
    (r,) = wifi.plan([wide])["radios"]
    assert r["htmode"] == "HE20"


def test_lineup_across_openwrt_and_fritzbox() -> None:
    survey = {
        "networks": [
            {
                "ssid": "Castle",
                "on": [
                    {
                        "device": "ap",
                        "section": "s",
                        "radio": "radio0",
                        "band": "2g",
                        "encryption": "sae-mixed",
                        "key": "f1",
                        "disabled": False,
                    }
                ],
            }
        ]
    }
    fritz = {
        "gw": [
            {"index": 1, "ssid": "Castle", "band": "2g", "encryption": "11iandWPA3", "enabled": True},
            {
                "index": 3,
                "ssid": "Guests",
                "band": "2g",
                "encryption": "11i",
                "enabled": False,
                "guest": True,
            },
        ]
    }
    nets = wifi.lineup(survey, fritz, {("gw", 1): "f2", ("gw", 3): "g"})
    castle, guests = nets
    assert castle["devices"] == ["ap", "gw"] and castle["same_encryption"] and not castle["same_key"]
    assert guests["guest"] and guests["off"] == ["gw"]


def test_the_daily_scan_is_due_once_after_its_time() -> None:
    from datetime import datetime

    from kastellan.wifi_api import due

    at = datetime(2026, 11, 21, 4, 5)
    assert due("04:00", None, at)
    assert not due("04:00", "2026-11-21", at)  # once a day
    assert due("04:00", "2026-11-20", at)
    assert not due("04:00", None, datetime(2026, 11, 21, 3, 59))
    assert not due("04:00", None, datetime(2026, 11, 21, 9, 0))  # a hub started hours later waits
    assert not due(None, None, at)


async def test_router_can_ignore_ping_while_the_internet_works(pings) -> None:
    pings |= {"1.1.1.1": [10] * 4, "9.9.9.9": [20] * 4, "dns": [1]}
    s = await internet.look(probe(line=False))
    assert s.state == "up" and s.jitter == 0  # jitter is measured within each destination


def test_unknown_channels_and_passwords_are_not_assumed_safe() -> None:
    r = wifi.Radio("ap", "radio0", "2g", 6, 20)
    assert wifi.candidates(r, False) == [6]
    r.allowed = []
    assert wifi.candidates(r, False) == []
    nets = wifi.lineup({}, {"gw": [{"index": 1, "ssid": "home", "encryption": "11i"}]}, {})
    assert nets[0]["same_key"] is None


async def test_speedtest_sends_exact_size_and_download_has_a_deadline(monkeypatch) -> None:
    import asyncio

    import httpx

    bodies = []

    async def upload(request):
        bodies.append(await request.aread())
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upload)) as client:
        await internet._upload(client, "https://test.invalid/up", 100001)
    assert len(bodies[0]) == 100001

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(5)
            yield b"never"

    monkeypatch.setattr(internet, "MAX_TEST", 0.01)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stalled()))
    ) as client:
        with pytest.raises(TimeoutError):
            await internet._download(client, "https://test.invalid/down")
