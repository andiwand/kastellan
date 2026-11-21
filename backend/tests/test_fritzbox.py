from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
import pytest

from kastellan.fritzbox import client as fb
from kastellan.openwrt.client import Pins

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
]


class FakeBox:
    """TR-064 as far as Kastellan uses it: Digest auth (MD5, qop=auth), SOAP, AVM's answers."""

    def __init__(self, password: str = "pw", second_factor: bool = False) -> None:
        self.password = password
        self.second_factor = second_factor
        self.calls: list[tuple[str, dict]] = []
        self.ssid = {1: "Castle", 2: "Castle", 3: "Castle guests"}
        self.key = {1: "k1", 2: "k1", 3: "guest"}
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
                    "NewUpTime": "99",
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
    with pytest.raises(ValueError):
        await fb.change_wifi(b, 2, {"key": "short"})
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
