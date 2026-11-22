from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
import pytest

from kastellan.fritzbox import client as fb
from kastellan.pinned_http import Pins

DESC = """<?xml version="1.0"?>
<root xmlns="urn:dslforum-org:device-1-0"><device>
<modelName>FRITZ!Box 7590</modelName><serviceList>
{services}
</serviceList></device></root>"""
SERVICES = [
    "DeviceInfo:1",
    "DeviceConfig:1",
    "UserInterface:1",
    "WANCommonInterfaceConfig:1",
    "WANPPPConnection:1",
    "WANDSLInterfaceConfig:1",
    "Hosts:1",
    "WLANConfiguration:1",
    "WLANConfiguration:2",
    "WLANConfiguration:3",
    "X_AVM-DE_RemoteAccess:1",
    "X_AVM-DE_Auth:1",
]


class FakeBox:
    """TR-064 as far as Kastellan uses it: Digest auth (MD5, qop=auth), SOAP, AVM's answers."""

    def __init__(self, password: str = "pw", second_factor: bool = False) -> None:
        self.password = password
        self.second_factor = second_factor
        self.calls: list[tuple[str, dict]] = []
        self.ssid = {1: "Castle", 2: "Castle", 3: "Castle guests"}
        self.key = {1: "k1", 2: "k1", 3: "guest"}
        self.uptime = "99"
        self.log = "21.11.26 10:00:02 WLAN-Gerät angemeldet\n21.11.26 09:59:00 Anmeldung fehlgeschlagen\n"

    def authorized(self, request: httpx.Request) -> bool:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Digest "):
            return False
        f = dict(re.findall(r'(\w+)="?([^",]*)"?', auth))
        ha1 = hashlib.md5(f"{f['username']}:F!Box:{self.password}".encode()).hexdigest()
        ha2 = hashlib.md5(f"{request.method}:{f['uri']}".encode()).hexdigest()
        want = hashlib.md5(f"{ha1}:{f['nonce']}:{f['nc']}:{f['cnonce']}:auth:{ha2}".encode()).hexdigest()
        return f["response"] == want and f["uri"] == request.url.raw_path.decode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.authorized(request):
            return httpx.Response(
                401,
                headers={"WWW-Authenticate": 'Digest realm="F!Box", nonce="n1", algorithm=MD5, qop="auth"'},
            )
        path = request.url.path
        if path == "/tr64desc.xml":
            services = "".join(
                f"<service><serviceType>urn:dslforum-org:service:{s.split(chr(58))[0]}:1</serviceType>"
                f"<serviceId>urn:WLANConfiguration-com:serviceId:WLANConfiguration{s.split(chr(58))[1]}</serviceId>"
                f"<controlURL>/upnp/control/{s.split(':')[0].lower()}{s.split(':')[1]}</controlURL></service>"
                for s in SERVICES
            )
            return httpx.Response(200, text=DESC.format(services=services))
        if path == "/hosts.lua":
            return httpx.Response(
                200,
                text="<List><Item><MACAddress>AA:BB:CC:00:11:22</MACAddress><IPAddress>192.168.178.40</IPAddress>"
                "<HostName>Pixel</HostName><Active>1</Active><InterfaceType>802.11</InterfaceType></Item>"
                "<Item><HostName>gone</HostName><Active>0</Active></Item></List>",
            )
        if path == "/export":
            return httpx.Response(200, text="**** FRITZ!Box 7590 CONFIGURATION EXPORT\nPassword=$$$$X\n")
        action = request.headers["soapaction"].strip('"').split("#")[1]
        service = (
            request.headers["soapaction"].strip('"').split("#")[0].removeprefix("urn:dslforum-org:service:")
        )
        args = dict(re.findall(r"<(New[\w-]+)>(.*?)</\1>", request.content.decode()))
        n = int(path.removeprefix("/upnp/control/wlanconfiguration")) if service.startswith("WLAN") else 0
        if n:
            assert service == "WLANConfiguration:1"
            service = f"WLANConfiguration:{n}"
        self.calls.append((f"{service}#{action}", args))
        if self.second_factor and action.startswith("Set"):
            return self.fault(866)
        answers = {
            "GetInfo": {
                "DeviceInfo": {
                    "NewModelName": "FRITZ!Box 7590",
                    "NewSoftwareVersion": "154.08.20",
                    "NewUpTime": self.uptime,
                },
                "UserInterface": {"NewUpgradeAvailable": "1", "NewX_AVM-DE_Version": "FRITZ!OS 8.21"},
                "WANPPPConnection": {
                    "NewConnectionStatus": "Connected",
                    "NewUptime": "4000",
                    "NewExternalIPAddress": "203.0.113.48",
                },
                "WANDSLInterfaceConfig": {
                    "NewStatus": "Up",
                    "NewUpstreamCurrRate": "46720",
                    "NewDownstreamCurrRate": "250000",
                },
                "WLANConfiguration": {
                    "NewSSID": self.ssid.get(n, ""),
                    "NewEnable": "1",
                    "NewChannel": "6",
                    "NewBeaconType": "11iandWPA3",
                    "NewStatus": "Up",
                },
                "X_AVM-DE_RemoteAccess": {"NewEnabled": "0", "NewPort": "443"},
            }.get(service.split(":")[0], {}),
            "GetCommonLinkProperties": {"NewWANAccessType": "DSL", "NewPhysicalLinkStatus": "Up"},
            "X_AVM-DE_GetHostListPath": {"NewX_AVM-DE_HostListPath": "/hosts.lua?sid=abc"},
            "GetPortMappingNumberOfEntries": {"NewPortMappingNumberOfEntries": "1"},
            "GetGenericPortMappingEntry": {
                "NewPortMappingEnabled": "1",
                "NewProtocol": "UDP",
                "NewExternalPort": "51820",
                "NewInternalClient": "192.168.178.10",
                "NewInternalPort": "51820",
            },
            "X_AVM-DE_GetWLANExtInfo": {
                "NewX_AVM-DE_FrequencyBand": "5000" if n == 2 else "2400",
                "NewX_AVM-DE_APType": "guest" if n == 3 else "normal",
            },
            "GetTotalAssociations": {"NewTotalAssociations": "2"},
            "GetSecurityKeys": {"NewKeyPassphrase": self.key.get(n, "")},
            "GetDeviceLog": {"NewDeviceLog": self.log},
            "X_AVM-DE_GetConfigFile": {"NewX_AVM-DE_ConfigFileUrl": "https://fritz.box:49443/export"},
        }
        if action == "SetSSID":
            self.ssid[n] = args["NewSSID"]
        if action == "SetSecurityKeys":
            self.key[n] = args["NewKeyPassphrase"]
        if action not in answers and not action.startswith(("Set", "Reboot", "X_AVM-DE_Set", "X_AVM-DE_Do")):
            return self.fault(401)
        out = "".join(f"<{k}>{v}</{k}>" for k, v in answers.get(action, {}).items())
        return httpx.Response(
            200,
            text=f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f'<u:{action}Response xmlns:u="urn:dslforum-org:service:{service}">{out}</u:{action}Response>'
            "</s:Body></s:Envelope>",
        )

    def fault(self, code: int) -> httpx.Response:
        return httpx.Response(
            500,
            text='<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
            '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
            f"<errorCode>{code}</errorCode><errorDescription>x</errorDescription></UPnPError></detail>"
            "</s:Fault></s:Body></s:Envelope>",
        )


def box(tmp_path: Path, fake: FakeBox, password: str = "pw") -> fb.Fritz:
    return fb.Fritz(
        "gw",
        "192.168.178.1",
        49443,
        "kastellan",
        password,
        Pins(tmp_path),
        transport=httpx.MockTransport(fake.handle),
    )


async def test_reading_the_box(tmp_path: Path) -> None:
    fake = FakeBox()
    report = await fb.read(box(tmp_path, fake))
    assert report["model"] == "FRITZ!Box 7590" and report["update"] == {
        "available": True,
        "version": "FRITZ!OS 8.21",
    }
    net = report["internet"]
    assert (net["connection"], net["address"], net["dsl"]["down"]) == ("Connected", "203.0.113.48", 250000)
    assert [(w["index"], w["band"], w["guest"]) for w in report["wifi"]] == [
        (1, "2g", False),
        (2, "5g", False),
        (3, "2g", True),
    ]
    assert report["clients"] == [
        {
            "name": "Pixel",
            "mac": "aa:bb:cc:00:11:22",
            "address": "192.168.178.40",
            "via": "802.11",
            "speed": None,
            "guest": False,
        }
    ]
    assert report["forwards"][0]["port"] == 51820 and report["remote"]["remote_access"] is False


async def test_a_wrong_password_is_refused(tmp_path: Path) -> None:
    with pytest.raises(fb.LoginRefused):
        await fb.read(box(tmp_path, FakeBox(), password="wrong"))


async def test_changing_wifi_and_the_second_factor(tmp_path: Path) -> None:
    fake = FakeBox()
    b = box(tmp_path, fake)
    await fb.change_wifi(b, 2, {"ssid": "Castle 5", "key": "a-new-passphrase", "channel": 0})
    assert fake.ssid[2] == "Castle 5" and fake.key[2] == "a-new-passphrase"
    assert ("WLANConfiguration:2#SetChannel", {"NewChannel": "0"}) in fake.calls
    fake.second_factor = True
    with pytest.raises(fb.FritzError, match="confirm it on the FRITZ!Box"):
        await fb.change_wifi(b, 1, {"ssid": "x"})


async def test_the_event_log_after_its_cursor(tmp_path: Path) -> None:
    fake = FakeBox()
    text = await fb.device_log(box(tmp_path, fake))
    lines, cursor = fb.log_lines("gw", text, None)
    assert [ln.msg for ln in lines] == ["Anmeldung fehlgeschlagen", "WLAN-Gerät angemeldet"]  # oldest first
    assert lines[0].level == 4 and lines[1].level == 6
    assert fb.log_lines("gw", text, cursor) == ([], cursor)
    newer = "21.11.26 10:05:00 Internetverbindung hergestellt\n" + text
    lines, _ = fb.log_lines("gw", newer, cursor)
    assert [ln.msg for ln in lines] == ["Internetverbindung hergestellt"]


async def test_the_export_is_fetched_from_the_box_itself(tmp_path: Path) -> None:
    fake = FakeBox()
    text = await fb.config_export(box(tmp_path, fake), "export-pw")
    assert text.startswith(b"**** FRITZ!Box 7590 CONFIGURATION EXPORT")
    assert ("DeviceConfig:1#X_AVM-DE_GetConfigFile", {"NewX_AVM-DE_Password": "export-pw"}) in fake.calls


async def test_invalid_wifi_request_changes_nothing(tmp_path: Path) -> None:
    fake = FakeBox()
    with pytest.raises(ValueError):
        await fb.change_wifi(box(tmp_path, fake), 1, {"ssid": "New name", "key": "short"})
    assert not fake.calls


async def test_wps_refusal_is_reported(tmp_path: Path) -> None:
    fake = FakeBox()
    original = fake.handle

    def refuse(request):
        if "SetWPSEnable" in request.headers.get("soapaction", ""):
            return fake.fault(606)
        return original(request)

    client = box(tmp_path, fake)
    client.transport = httpx.MockTransport(refuse)
    with pytest.raises(fb.FritzError, match="not allowed"):
        await fb.wps_off(client)


async def test_remote_request_paths_cannot_inject_http(tmp_path: Path) -> None:
    b = box(tmp_path, FakeBox())
    with pytest.raises(ValueError, match="path"):
        await b.request("GET", "/file\r\nAuthorization: stolen")


async def test_failed_setup_save_preserves_the_live_login(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from starlette.requests import Request

    from kastellan import fritzbox_api, setup_run
    from kastellan.auth_api import Who
    from kastellan.config import Config, Role
    from kastellan.secrets import Secrets

    path = tmp_path / "config.yaml"
    path.write_text("devices:\n  gw:\n    host: fritz.box\n    fritzbox: {user: old}\n")
    cfg = Config.model_validate({"devices": {"gw": {"host": "fritz.box", "fritzbox": {"user": "old"}}}})
    secrets = Secrets(tmp_path / "secrets")
    secrets.set("fritzbox-gw", "old-password")
    pins = Pins(tmp_path / "pins")
    pins.set("gw", "a" * 64)
    hub = SimpleNamespace(
        config=cfg,
        settings=SimpleNamespace(config=path),
        demo=False,
        secrets=secrets,
        fritz_pins=pins,
        _fritz={},
        check=lambda _: None,
        audit=SimpleNamespace(record=lambda *a, **kw: None),
    )

    async def certificate(*args):
        return "b" * 64

    async def call(*args):
        return {"NewModelName": "FRITZ!Box"}

    def save(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(fb, "certificate", certificate)
    monkeypatch.setattr(fb.Fritz, "call", call)
    monkeypatch.setattr(setup_run, "add_device_key", save)
    route = next(r for r in fritzbox_api.router(hub).routes if r.path.endswith("/fritzbox/setup"))
    request = Request({"type": "http", "headers": [], "state": {"who": Who("admin", Role.ADMIN)}})
    body = fritzbox_api.Setup(user="new", password="new-password", fingerprint="b" * 64)
    with pytest.raises(OSError, match="disk full"):
        await route.endpoint("gw", body, request)
    assert secrets.get("fritzbox-gw") == "old-password"
    assert pins.get("gw") == "a" * 64


def test_the_web_login_answer() -> None:
    from kastellan.fritzbox.web import response

    # AVM's own example (login_sid.lua, PBKDF2 since FRITZ!OS 7.24).
    assert (
        response("2$10000$5A1711$2000$5A1722", "1example!")
        == "5A1722$1798a1672bca7c6463d6b245f82b53703b0f50813401b03e4045a5861e689adb"
    )
    assert response("1234567z", "äbc").startswith("1234567z-")  # MD5, before 7.24


def test_the_wifi_environment_and_wireguard_as_fritzos_8_25_shows_them() -> None:
    from kastellan.fritzbox import web

    data = {
        "channelSelectMode": "auto",
        "scanlist": [
            {"mac": "AA:00:00:00:00:01", "nrssi": 5, "channel": "1", "ssid": "Stefl", "bandId": "24ghz"},
            {"mac": "AA:00:00:00:00:02", "nrssi": 1, "channel": 6, "ssid": "Neighbour", "bandId": "24ghz"},
            {"mac": "AA:00:00:00:00:03", "nrssi": 0, "channel": 52, "ssid": "Far", "bandId": "5ghz"},
        ],
        "24ghz": {
            "usedChannels": [1],
            "currentChannel": 0,
            "airtimedata": "1795290069,1,600,14:5,20:6,30:4",
            "channels": [{"value": 1, "dfs": False, "interference": False}],
        },
        "5ghz": {"usedChannels": [52], "airtimedata": "", "channels": [{"value": 52, "dfs": True}]},
    }
    env = web.environment(data)
    assert env["2g"]["channel"] == 1 and env["2g"]["busy"] == 21 and env["2g"]["auto"]
    assert [(n["ssid"], n["channel"], n["signal"]) for n in env["2g"]["neighbours"]] == [
        ("Neighbour", 6, -80)
    ]
    assert env["5g"]["channels"] == [{"channel": 52, "dfs": True, "interference": False}]
    wg = web.wireguard(
        {
            "init": {
                "server": "x.myfritz.net",
                "wg_list_port": "51820",
                "userConnections": [{"name": "phone", "active": True, "lastHandshake": "21.11.2026 10:00"}],
                "boxConnections": [],
            }
        }
    )
    assert wg["port"] == "51820" and wg["connections"][0]["name"] == "phone"
    with pytest.raises(fb.FritzError, match="can't be read"):
        web.wireguard({})  # an unknown schema is not an empty connection list
    lists = {"userConnections": [], "boxConnections": []}
    assert (
        web.wireguard({"init": lists | {"wg_global_error": {"eventMsg": "no route"}}})["error"] == "no route"
    )
    assert web.wireguard({"init": lists | {"wg_global_error": "odd"}})["error"] is None


async def test_the_mesh_and_each_networks_clients(tmp_path: Path) -> None:
    import json

    fake = FakeBox()
    mesh = {
        "nodes": [
            {
                "uid": "n1",
                "device_name": "gw1",
                "device_model": "FRITZ!Box 7530",
                "is_meshed": True,
                "mesh_role": "master",
                "node_interfaces": [
                    {
                        "type": "WLAN",
                        "node_links": [
                            {
                                "uid": "l1",
                                "node_1_uid": "n1",
                                "node_2_uid": "n2",
                                "state": "CONNECTED",
                                "cur_data_rate_rx": 866000,
                                "cur_data_rate_tx": 650000,
                                "rx_rcpi": 100,
                            }
                        ],
                    }
                ],
            },
            {
                "uid": "n2",
                "device_name": "rep1",
                "is_meshed": True,
                "mesh_role": "slave",
                "node_interfaces": [],
            },
            {"uid": "n3", "device_name": "phone", "is_meshed": False, "node_interfaces": []},
        ]
    }
    original = fake.handle

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mesh.json" and fake.authorized(request):
            return httpx.Response(200, text=json.dumps(mesh))
        if request.url.path == "/wlan.lua" and fake.authorized(request):
            return httpx.Response(
                200,
                text="<List><Item><AssociatedDeviceMACAddress>AA:BB:CC:00:11:22"
                "</AssociatedDeviceMACAddress><X_AVM-DE_SignalStrength>70"
                "</X_AVM-DE_SignalStrength><X_AVM-DE_Speed>866</X_AVM-DE_Speed></Item></List>",
            )
        if fake.authorized(request) and b"GetMeshListPath" in request.headers.get("soapaction", "").encode():
            return httpx.Response(
                200,
                text='<s:Envelope xmlns:s="x"><s:Body><u:X_AVM-DE_GetMeshListPathResponse '
                'xmlns:u="y"><NewX_AVM-DE_MeshListPath>/mesh.json</NewX_AVM-DE_MeshListPath>'
                "</u:X_AVM-DE_GetMeshListPathResponse></s:Body></s:Envelope>",
            )
        if (
            fake.authorized(request)
            and b"GetWLANDeviceListPath" in request.headers.get("soapaction", "").encode()
        ):
            return httpx.Response(
                200,
                text='<s:Envelope xmlns:s="x"><s:Body><u:X_AVM-DE_GetWLANDeviceListPathResponse '
                'xmlns:u="y"><NewX_AVM-DE_WLANDeviceListPath>/wlan.lua</NewX_AVM-DE_WLANDeviceListPath>'
                "</u:X_AVM-DE_GetWLANDeviceListPathResponse></s:Body></s:Envelope>",
            )
        return original(request)

    b = fb.Fritz(
        "gw", "192.168.178.1", 49443, "kastellan", "pw", Pins(tmp_path), transport=httpx.MockTransport(handle)
    )
    report = await fb.read(b)
    assert [(m["name"], m["role"]) for m in report["mesh"]["members"]] == [
        ("gw1", "master"),
        ("rep1", "repeater"),
    ]
    (link,) = report["mesh"]["links"]
    assert (link["type"], link["rate_rx"], link["signal"]) == ("WLAN", 866000, -60)
    assert report["wifi"][0]["associated"] == [
        {"mac": "aa:bb:cc:00:11:22", "address": None, "signal": 70, "speed": 866}
    ]
    await fb.change_wifi(b, 1, {"encryption": "sae", "hidden": True})
    assert ("WLANConfiguration:1#SetBeaconType", {"NewBeaconType": "WPA3"}) in fake.calls
    assert (
        "WLANConfiguration:1#SetBeaconAdvertisement",
        {"NewBeaconAdvertisementEnabled": "0"},
    ) in fake.calls
    with pytest.raises(ValueError):
        await fb.change_wifi(b, 1, {"encryption": "owe"})


async def test_the_second_factor_is_waited_for(tmp_path: Path) -> None:
    fake = FakeBox(second_factor=True)
    b = box(tmp_path, fake)
    states = iter(["waitingforauth", "authenticated"])
    original = fake.handle

    def handle(request: httpx.Request) -> httpx.Response:
        action = request.headers.get("soapaction", "")
        if fake.authorized(request) and "X_AVM-DE_Auth" in action:
            name = action.strip('"').split("#")[1]
            if name == "GetState" or b">stop<" in request.content:
                assert (
                    b'<avm:token xmlns:avm="avm.de" s:mustUnderstand="1">test-token</avm:token>'
                    in request.content
                )
            out = (
                "<NewState>waitingforauth</NewState><NewMethods>button</NewMethods><NewToken>test-token</NewToken>"
                if name == "SetConfig"
                else f"<NewState>{next(states)}</NewState>"
            )
            if name == "GetState" and "authenticated" in out:
                fake.second_factor = False
            return httpx.Response(
                200,
                text=f'<s:Envelope xmlns:s="x"><s:Body><u:{name}Response xmlns:u="y">{out}'
                f"</u:{name}Response></s:Body></s:Envelope>",
            )
        if fake.authorized(request) and "SetSSID" in action and not fake.second_factor:
            assert b">test-token</avm:token>" in request.content
        return original(request)

    b.transport = httpx.MockTransport(handle)
    said = []
    await fb.confirmed(b, lambda: fb.change_wifi(b, 1, {"ssid": "x"}), wait=5, say=said.append, poll=0)
    assert fake.ssid[1] == "x" and "button" in said[0]
    assert b._token.get() is None  # no token on unrelated requests after the operation
    fake.second_factor = True
    with pytest.raises(fb.FritzError):  # nobody at the box (a scheduled run): no waiting
        await fb.confirmed(b, lambda: fb.change_wifi(b, 1, {"ssid": "y"}), wait=0)


async def test_what_rarely_changes_is_asked_less_often(tmp_path: Path) -> None:
    fake = FakeBox()
    b = box(tmp_path, fake)
    first = await fb.read(b)
    n = len(fake.calls)
    again = await fb.read(b)
    later = [c for c, _ in fake.calls[n:]]
    assert again["forwards"] == first["forwards"] and again["wifi"] == first["wifi"]
    assert "DeviceInfo:1#GetInfo" in later  # the line and uptime: every round
    assert not any(
        c.startswith(("UserInterface", "WLANConfiguration", "Hosts", "X_AVM-DE_HostFilter")) for c in later
    )
    assert "LANHostConfigManagement:1#GetInfo" not in later
    async with b.changing:  # a change of Kastellan's: read anew
        pass
    m = len(fake.calls)
    await fb.read(b)
    assert any(c.startswith("WLANConfiguration") for c, _ in fake.calls[m:])
    # A lower uptime: the box restarted (new FRITZ!OS perhaps), so the kept sections and its
    # service list are read again.
    services = b._services
    fake.uptime = "5"
    m = len(fake.calls)
    await fb.read(b)
    assert "UserInterface:1#GetInfo" in [c for c, _ in fake.calls[m:]]
    assert b._services is not services


async def test_the_action_after_a_confirmation_has_its_own_time(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    b = box(tmp_path, FakeBox())

    async def call(service, action, args=None, timeout=30):
        return {"NewToken": "t", "NewState": "authenticated"} if action == "SetConfig" else {}

    monkeypatch.setattr(b, "call", call)
    tries = []

    async def action():
        tries.append(1)
        if len(tries) == 1:
            raise fb.FritzError(866, "confirm it")
        await asyncio.sleep(0.1)  # longer than the confirmation may take
        return "done"

    assert await fb.confirmed(b, action, wait=0.05, poll=0) == "done"


async def test_an_old_read_cannot_repopulate_an_invalidated_cache(tmp_path: Path) -> None:
    import asyncio

    b = box(tmp_path, FakeBox())
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def read(errors):
        calls.append(1)
        started.set()
        await finish.wait()
        return len(calls)

    task = asyncio.create_task(fb._kept(b, "wifi", {}, read))
    await started.wait()
    async with b.changing:
        pass
    finish.set()
    assert await task == 1
    assert await fb._kept(b, "wifi", {}, read) == 2
    assert await fb._kept(b, "wifi", {}, read) == 2


def test_replacing_a_client_clears_its_wireguard_backoff(tmp_path: Path) -> None:
    from kastellan.hub import Hub
    from kastellan.settings import Settings

    hub = Hub(Settings(config=tmp_path / "config.yaml", data=tmp_path))
    hub._wg_next = {"a": (9999999, 5), "b": (9999999, 4)}
    hub.drop_clients("a", fritz=False)
    assert "a" in hub._wg_next
    hub.drop_clients("a")
    assert set(hub._wg_next) == {"b"}
    hub.drop_clients()
    assert not hub._wg_next
    hub.logs.close()
