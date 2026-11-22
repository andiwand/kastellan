from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from kastellan import internet, wifi
from kastellan.internet import Internet, Probe, Sample


async def test_traceroute_uses_supported_icmplib_arguments(monkeypatch) -> None:
    import icmplib

    def trace(address, **kwargs):
        # traceroute forwards extra kwargs into ICMPRequest, not into its raw socket.
        icmplib.ICMPRequest(
            destination=address,
            id=1,
            sequence=1,
            **{k: v for k, v in kwargs.items() if k not in {"count", "interval", "timeout", "max_hops"}},
        )
        return []

    monkeypatch.setattr(icmplib, "traceroute", trace)
    assert await internet.route("1.1.1.1") == []


async def test_cancelled_iperf_is_killed_and_reaped(monkeypatch) -> None:
    from unittest.mock import AsyncMock, Mock

    begun = asyncio.Event()

    async def communicate():
        begun.set()
        await asyncio.Event().wait()

    process = Mock(returncode=None, communicate=communicate, wait=AsyncMock())
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/iperf3")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(internet._iperf3("example.org", False))
    await begun.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


def test_stale_bandwidth_is_not_shown_as_current() -> None:
    from types import SimpleNamespace

    from kastellan.internet_api import clients_view

    bw = {"t": time.time(), "clients": {"aa": {"rx": 100, "tx": 200, "rx_rate": 8, "tx_rate": 16}}}
    hub = SimpleNamespace(
        config=SimpleNamespace(devices={"gw": None}), monitor=SimpleNamespace(status={}), bandwidth={"gw": bw}
    )
    assert clients_view(hub)["rows"][0]["rx_rate"] == 8
    bw["error"] = "unreachable"
    assert clients_view(hub)["rows"][0]["rx_rate"] is None
    del bw["error"]
    bw["t"] -= 301
    assert clients_view(hub)["rows"][0]["rx_rate"] is None


async def test_wifi_cache_respects_config_reload_and_locked_keys(tmp_path, monkeypatch):
    from starlette.requests import Request

    from kastellan import wifi_api
    from kastellan.auth_api import Who
    from kastellan.config import Config, Role
    from kastellan.hub import Hub
    from kastellan.settings import Settings
    from kastellan.vault import Locked

    hub = Hub(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    hub.config = Config.model_validate({"devices": {"ap": {"host": "ap", "openwrt": {}}}})
    monkeypatch.setattr(hub, "ubus", lambda id: object())
    calls = []

    async def survey(members, key):
        calls.extend(members)
        return {"errors": {}, "networks": []}

    monkeypatch.setattr(wifi_api.sync, "survey", survey)
    monkeypatch.setattr(hub, "own_secret", lambda *args: "salt")
    overview = next(r.endpoint for r in wifi_api.router(hub).routes if r.path == "/api/wifi")
    request = Request({"type": "http", "headers": [], "state": {"who": Who("admin", Role.ADMIN)}})
    await overview(request)
    await overview(request)
    assert calls == ["ap"]
    hub.config = Config.model_validate({"devices": {"new": {"host": "new", "openwrt": {}}}})
    await overview(request)
    assert calls == ["ap", "new"]

    def locked(*args):
        raise Locked("kastellan")

    monkeypatch.setattr(hub, "own_secret", locked)
    with pytest.raises(Locked):
        await overview(request)
    monkeypatch.setattr(hub, "own_secret", lambda *args: "salt")
    await overview(request)
    assert calls == ["ap", "new", "new"]

    # Stale, but the page refreshing on its own: what is kept, no access point asked.
    monkeypatch.setattr(wifi_api, "LIVE_FRESH", 0.0)
    auto = Request(
        {"type": "http", "headers": [(b"x-kastellan-auto", b"1")], "state": {"who": Who("admin", Role.ADMIN)}}
    )
    await overview(auto)
    assert calls == ["ap", "new", "new"]
    await overview(request)  # someone looks: read anew (shown next time; it runs alongside)
    await asyncio.sleep(0.05)
    assert calls == ["ap", "new", "new", "new"]


async def test_a_wifi_reading_under_way_survives_its_invalidation(tmp_path, monkeypatch):
    """A reading begun before a change still answers who waits for it, but isn't kept."""
    from starlette.requests import Request

    from kastellan import wifi_api
    from kastellan.auth_api import Who
    from kastellan.config import Config, Role
    from kastellan.hub import Hub
    from kastellan.settings import Settings

    hub = Hub(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    hub.config = Config.model_validate({"devices": {"ap": {"host": "ap", "openwrt": {}}}})
    monkeypatch.setattr(hub, "ubus", lambda id: object())
    monkeypatch.setattr(hub, "own_secret", lambda *args: "salt")
    gate = asyncio.Event()

    async def survey(members, key):
        if "ap" in members:
            await gate.wait()
        return {"errors": dict.fromkeys(members, "read"), "networks": []}

    monkeypatch.setattr(wifi_api.sync, "survey", survey)
    overview = next(r.endpoint for r in wifi_api.router(hub).routes if r.path == "/api/wifi")
    request = Request({"type": "http", "headers": [], "state": {"who": Who("admin", Role.ADMIN)}})
    first = asyncio.create_task(overview(request))
    await asyncio.sleep(0.01)
    hub.config = Config.model_validate({"devices": {"new": {"host": "new", "openwrt": {}}}})
    assert (await overview(request))["errors"] == {"new": "read"}
    gate.set()
    assert (await first)["errors"] == {"ap": "read"}
    assert (await overview(request))["errors"] == {"new": "read"}


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
    pings |= {"192.168.178.1": [], "1.1.1.1": [10] * 4, "9.9.9.9": [20] * 4, "dns": [1]}
    s = await internet.look(probe(line=False))  # a router may ignore pings while the internet works
    assert s.state == "up" and s.jitter == 0  # jitter is measured within each destination


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


def test_unknown_channels_and_passwords_are_not_assumed_safe() -> None:
    r = wifi.Radio("ap", "radio0", "2g", 6, 20)
    assert wifi.candidates(r, False) == [6]
    r.allowed = []
    assert wifi.candidates(r, False) == []
    nets = wifi.lineup({}, {"gw": [{"index": 1, "ssid": "home", "encryption": "11i"}]}, {})
    assert nets[0]["same_key"] is None


async def test_speedtest_sends_exact_size_and_download_has_a_deadline(monkeypatch) -> None:
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


def test_provider_hops_are_after_the_home_network() -> None:
    route = [
        {"distance": 1, "address": "192.168.1.1"},
        {"distance": 2, "address": "10.0.0.1"},
        {"distance": 3, "address": "100.70.0.1"},
        {"distance": 4, "address": "62.155.240.17"},
        {"distance": 5, "address": "80.1.1.1"},
    ]
    assert internet.provider_hops(route, "192.168.1.1") == ["100.70.0.1", "62.155.240.17"]
    assert internet.provider_hops([], None) == []


async def test_an_outage_at_the_provider_is_told_apart(pings) -> None:
    pings["192.168.1.1"] = [1.0] * 4  # the router answers; nothing beyond it, the provider's hop neither
    p = internet.Probe("192.168.1.1", True, None, ["1.1.1.1"], [], ["example.com"], hops=["100.70.0.1"])
    s = await internet.look(p, now=1.0)
    assert (s.state, s.step) == ("down", "provider") and s.hop_loss == 1.0
    p.hops = []
    assert (await internet.look(p, now=1.0)).step == "internet"


def test_iperf3_results_are_read() -> None:
    import json

    from kastellan.config import SpeedTest

    out = {"end": {"sum_received": {"bits_per_second": 93_500_000.5}, "sum_sent": {}}}
    assert internet.iperf3_rate(json.dumps(out)) == 93_500_000
    with pytest.raises(RuntimeError, match="busy"):
        internet.iperf3_rate(json.dumps({"error": "the server is busy running a test"}))
    assert SpeedTest(iperf3="iperf.example.net:5202").iperf3
    with pytest.raises(ValueError):
        SpeedTest(iperf3="-x evil")


def test_line_trouble(tmp_path) -> None:
    net = internet.Internet(tmp_path)
    for i in range(3):
        net._append("events.jsonl", {"t": 1000.0 + i, "kind": "reconnect", "text": "x"})
    net.note_errors(0, {"crc": 10, "errored_seconds": 100})
    net.note_errors(1800, {"crc": 50, "errored_seconds": 200})
    keys = [k for k, _, _ in net.trouble(2000, {"snr_down": 45})]
    assert keys == ["internet:reconnects", "internet:errors", "internet:margin"]
    net.note_errors(1900, {"crc": 1, "errored_seconds": 1})  # a resync: counted anew
    assert [k for k, _, _ in net.trouble(90000 + 2000, {"snr_down": 90})] == []


def test_wifi_clients_are_diagnosed() -> None:
    from kastellan.openwrt.client import assoc_client

    c = assoc_client(
        {
            "mac": "AA:BB:CC:DD:EE:FF",
            "signal": -80,
            "noise": -95,
            "rx": {"rate": 6500, "mhz": 20, "mcs": 0, "ht": True},
            "tx": {"rate": 13000, "mhz": 20, "he": True, "nss": 1},
            "expected_throughput": 8000,
        },
        {"aa:bb:cc:dd:ee:ff": "phone"},
    )
    assert (
        c["name"] == "phone"
        and c["snr"] == 15
        and c["tx"] == {"mbit": 13.0, "mhz": 20, "mcs": None, "nss": 1, "std": "he"}
    )
    assert wifi.diagnose(c | {"band": "5g"}) == ["weak signal (-80 dBm)", "slow (13 Mbit/s to it)"]
    near = {"signal": -45, "band": "2g", "tx": {"mbit": 144.0}}
    assert wifi.diagnose(near) == ["on 2.4 GHz while close (5 GHz would be faster)"]
    assert wifi.diagnose({"kind": "fritzbox", "signal": 20, "speed": 6}) == ["weak signal", "slow (6 Mbit/s)"]
    assert wifi.country_note(["DE"], [{"country": "DE"}]) is None
    assert "different" in wifi.country_note(["AT", "DE"], [{"country": "AT"}, {"country": "DE"}])
    assert "no country" in wifi.country_note(["00"], [{"country": "00"}])


def test_roaming_in_a_group() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from kastellan import roaming
    from kastellan.config import Config

    config = Config.model_validate(
        {
            "devices": {
                "a": {"host": "192.168.1.2", "openwrt": {}},
                "b": {"host": "192.168.1.3", "openwrt": {}},
            },
            "groups": {"aps": {"devices": ["a", "b"]}},
        }
    )

    def entry(d, ft=True, md="ab12", k=True, v=True):
        return {
            "device": d,
            "section": "default_radio0",
            "radio": "radio0",
            "encryption": "psk2",
            "ft": ft,
            "mobility_domain": md,
            "k": k,
            "v": v,
        }

    survey = {
        "networks": [
            {
                "ssid": "Castle",
                "devices": ["a", "b"],
                "on": [entry("a"), entry("b", ft=False, md=None)],
                "same_key": True,
                "same_encryption": True,
            }
        ],
        "radios": {
            "a": [{"section": "radio0", "band": "2g", "channel": "6"}],
            "b": [{"section": "radio0", "band": "2g", "channel": "6"}],
        },
        "mesh": [],
    }
    status = {
        "a": SimpleNamespace(
            router={
                "services": [{"name": "usteer", "running": True}],
                "system": {"memory": {"total": 128 * 2**20}},
            }
        ),
        "b": SimpleNamespace(
            router={
                "services": [{"name": "usteer", "running": False}],
                "system": {"memory": {"total": 32 * 2**20, "available": 4 * 2**20}},
            }
        ),
    }
    hub = SimpleNamespace(
        config=config,
        monitor=SimpleNamespace(status=status),
        resolver=SimpleNamespace(address=lambda h: None),
        roaming={
            "a": {"nodes": [{"own": True}], "clients": [], "config": {"roam_trigger_snr": 20}},
            "b": {
                "nodes": [{"own": True}, {"own": False, "host": "192.168.1.2"}],
                "config": {"roam_trigger_snr": 25},
            },
        },
    )
    for s in status.values():
        s.router_checked, s.router_error = datetime.now(UTC), None
    out = roaming.check(hub, ["a", "b"], survey)
    texts = " | ".join(f["text"] for f in out["findings"])
    assert "802.11r) is off on b" in texts and "share channel 6" in texts
    assert "usteer isn't running on b" in texts and "a doesn't see b" in texts and "32 MB" in texts
    assert "roam trigger differs" in texts
    assert out["members"]["b"]["sees"] == ["a"]
    assert roaming.assistance(survey) == {
        "b": {"default_radio0": {**roaming.FT_OPTIONS, "mobility_domain": "ab12"}}
    }
    keys = {p.key for p in roaming.problems(hub)}
    assert keys == {"roaming:aps:b:usteer", "roaming:aps:a:view", "roaming:aps:b:memory"}
    survey["networks"][0]["same_key"] = False
    assert roaming.assistance(survey) == {}
    status["b"].router["system"]["memory"]["available"] = 0
    assert roaming.check(hub, ["a", "b"], None)["members"]["b"]["free"] == 0
    for s in status.values():
        s.router_checked = None
    assert roaming.problems(hub) == []
    assert (
        roaming.mobility_domain("Castle") == roaming.mobility_domain("Castle")
        and len(roaming.mobility_domain("x")) == 4
    )


def test_the_demo_lists_every_wifi_client_its_radios_count(demo_client) -> None:
    """One truth: the Clients table holds as many clients as the radios say they have, the
    FRITZ!Box's too, named from its host list."""
    body = demo_client.get("/api/wifi").json()
    counted = sum(e.get("clients") or 0 for n in body["networks"] for e in n["on"] if e["kind"] == "fritzbox")
    fritz = [c for c in body["clients"] if c["kind"] == "fritzbox"]
    assert counted and len(fritz) == counted
    assert all(c["name"] for c in fritz)


def test_wifi_findings_say_what_is_off_and_what_fixes_it() -> None:
    def radio(device, radio, band, channel, width=20, kind="openwrt"):
        return {
            "device": device,
            "radio": radio,
            "band": band,
            "channel": channel,
            "width": width,
            "kind": kind,
        }

    networks = wifi.lineup(
        {
            "networks": [
                {
                    "ssid": "Castle",
                    "on": [
                        {
                            "device": "a",
                            "radio": "radio0",
                            "band": "2g",
                            "encryption": "psk-mixed",
                            "key": "k1",
                        },
                        {
                            "device": "b",
                            "radio": "radio0",
                            "band": "2g",
                            "encryption": "sae-mixed",
                            "key": "k2",
                        },
                    ],
                }
            ]
        },
        {
            "gw": [
                {"index": 1, "ssid": "Cafe", "band": "2g", "encryption": "None", "enabled": True, "wps": True}
            ]
        },
        {},
    )
    radios = [
        radio("a", "radio0", "2g", 6),
        radio("b", "radio0", "2g", 6),
        radio("gw", "wlan1", "2g", 3, 40, "fritzbox"),
        radio("a", "radio1", "5g", 36, 80),
        radio("b", "radio1", "5g", 36, 80),  # meshed with a's: one channel is right
    ]
    meshes = [{"mesh_id": "m", "on": [{"device": d, "radio": "radio1"} for d in ("a", "b")]}]
    found = wifi.findings(
        networks=networks,
        radios=radios,
        clients=[
            {"device": "a", "name": "phone", "issues": ["weak signal (-80 dBm)"]},
            {"device": "b", "issues": []},
        ],
        errors={"c": "not allowed"},
        country=None,
        meshes=meshes,
        fritz_mesh={
            "gw": {
                "members": [{"uid": "n1", "name": "fritz.box"}, {"uid": "n2", "name": "Repeater hall"}],
                "links": [{"a": "n1", "b": "n2", "type": "WLAN", "signal": -78}],
            }
        },
        roaming={
            "aps": {
                "findings": [
                    {
                        "kind": "channel",
                        "level": "warning",
                        "text": "2g: a, b share channel 6",
                        "devices": ["a", "b"],
                    },
                    {"kind": "view", "level": "warning", "text": "A doesn't see B", "devices": ["a"]},
                ]
            }
        },
        scanned=False,
        names={"a": "AP A", "b": "AP B", "gw": "Box"},
    )
    by = {f["key"]: f for f in found}
    assert by["key:Castle"]["action"] == {"kind": "network", "ssid": "Castle"}
    assert "WPA2/WPA3 on AP B" in by["encryption:Castle"]["text"] and "wpa:Castle" in by
    assert by["open:Cafe"]["level"] == "warning" and by["wps:gw"]["action"] == {
        "kind": "wps-off",
        "device": "gw",
    }
    (two4,) = [f for f in found if f["key"].startswith("channel:2g")]
    assert two4["text"] == "2.4 GHz: AP A (6), AP B (6) and Box (3) overlap"
    assert two4["devices"] == ["a", "b", "gw"] and two4["action"] == {"kind": "plan"}
    assert not [f for f in found if f["key"].startswith("channel:5g")]
    assert "grid:gw/wlan1" in by and "wide:gw/wlan1" in by
    assert by["mesh:gw:n1:n2"]["text"] == "Repeater hall: weak Wi-Fi link to fritz.box (-78 dBm)"
    assert by["error:c"]["action"] == {"kind": "setup", "device": "c"}
    assert by["clients"]["text"] == "1 client has a weak or slow link"
    assert by["scan"]["level"] == "info"
    # roaming's own channel finding gives way to the page's; its view finding names its alarm
    assert by["roaming:aps:view:a"]["alarms"] == ["roaming:aps:a:view"]
    assert not any(k.startswith("roaming:aps:channel") for k in by)
    assert [f["level"] for f in found] == sorted((f["level"] for f in found), key=lambda x: x != "warning")


def test_surveys_of_single_access_points_merge_into_one() -> None:
    def part(d, key):
        entry = {"device": d, "section": "s", "radio": "radio0", "band": "2g", "key": key}
        return {
            "errors": {},
            "networks": [{"ssid": "Castle", "on": [entry]}],
            "radios": {d: [{"section": "radio0", "band": "2g", "channel": "6"}]},
            "mesh": [{"mesh_id": "m", "on": [entry | {"radio": "radio1"}]}],
        }

    merged = wifi.merge([part("a", "k"), part("b", "k"), {"errors": {"c": "down"}, "networks": []}])
    (castle,) = merged["networks"]
    assert castle["devices"] == ["a", "b"] and merged["errors"] == {"c": "down"}
    assert sorted(merged["radios"]) == ["a", "b"] and merged["mesh"][0]["devices"] == ["a", "b"]
    only = wifi.of_members(merged, ["a"])
    assert only["networks"][0]["devices"] == ["a"] and list(only["radios"]) == ["a"]
    assert only["mesh"][0]["devices"] == ["a"]


def test_the_demo_wifi_page_finds_what_its_groups_page_says(demo_client) -> None:
    body = demo_client.get("/api/wifi").json()
    keys = {f["key"] for f in body["findings"]}
    assert {"key:Castle", "wps:fritzbox", "clients"} <= keys
    assert any(k.startswith("channel:2g") for k in keys) and any(k.startswith("roaming:aps:ft") for k in keys)
    group = demo_client.get("/api/groups/aps").json()
    assert next(n for n in group["networks"] if n["ssid"] == "Castle")["same_key"] is False
    assert all(r["state"] == "on" for r in body["radios"])
    assert {r["mesh"] for r in body["radios"] if r["kind"] == "openwrt" and r["band"] == "5g"} == {
        "castle-mesh"
    }
