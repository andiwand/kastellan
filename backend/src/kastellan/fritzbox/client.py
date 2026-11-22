"""A FRITZ!Box through TR-064 (SOAP) over HTTPS, port 49443, with a FRITZ!Box user of Kastellan's
own: what is read and done with it.

The FRITZ!Box's certificate is pinned by its fingerprint, like an OpenWrt device's: set when an
admin confirms it at setup (FRITZ!OS shows it under Internet → Permit Access → FRITZ!Box
Services), compared on every connection before a byte is sent. Digest authentication, so the
password never travels, and the connection is encrypted besides (Wi-Fi passwords are sent when
they are changed).

The user needs the right "FRITZ!Box settings" for most of it (TR-064 offers no finer rights);
what FRITZ!OS wants confirmed on the box itself (its second factor: a button or a phone) comes
back as `FritzError` with code 866 and says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import ssl
import time
import xml.etree.ElementTree as ET
from contextvars import ContextVar
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

import httpx

from ..dhcp import in_range
from ..logs import Line
from ..pinned_http import ChangedCertificate, Changing, Pins, Pool, fingerprint_of_der

MAX_RESPONSE = 16 * 1024 * 1024
NS = "urn:dslforum-org:service:"
# AVM's own error codes (besides UPnP's 401 invalid action, 402 invalid args, 606 not authorized).
ERRORS = {
    401: "no such action on this FRITZ!Box",
    402: "invalid arguments",
    501: "the action failed",
    606: "not allowed: give Kastellan's FRITZ!Box user the right “FRITZ!Box settings”",
    713: "no such entry",
    714: "no such entry",
    820: "internal error",
    866: "confirm it on the FRITZ!Box itself (its second factor: press a button there or use the"
    " phone), then try again",
    867: "the FRITZ!Box blocks this until its second factor is confirmed",
    868: "the FRITZ!Box is busy confirming another change",
}


class FritzError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.denied = code == 606


class LoginRefused(Exception):
    denied = False


def _digest(user: str, password: str, method: str, uri: str, challenge: str, nc: int) -> str:
    """An Authorization header for HTTP Digest (RFC 2617, MD5, qop=auth: what FRITZ!OS asks)."""
    fields = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge))
    realm, nonce = fields.get("realm", ""), fields.get("nonce", "")
    cnonce = os.urandom(8).hex()
    ha1 = hashlib.md5(f"{user}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
    ncs = f"{nc:08x}"
    if "auth" in fields.get("qop", ""):
        response = hashlib.md5(f"{ha1}:{nonce}:{ncs}:{cnonce}:auth:{ha2}".encode()).hexdigest()
        return (
            f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
            f'response="{response}", algorithm=MD5, qop=auth, nc={ncs}, cnonce="{cnonce}"'
        )
    response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}"'


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class Fritz:
    """One FRITZ!Box's TR-064, with Kastellan's user; the service list is read once (and again
    after the box restarted: new FRITZ!OS may bring others)."""

    def __init__(
        self,
        id: str,
        host: str,
        port: int,
        user: str,
        password: str,
        pins: Pins,
        transport: httpx.AsyncBaseTransport | None = None,  # tests
    ) -> None:
        self.id, self.host, self.port, self.user, self.password = id, host, port, user, password
        self.pins, self.transport = pins, transport
        self._services: dict[str, str] | None = None
        self._service_types: dict[str, str] = {}
        self._challenge: str | None = None
        self._nc = 0
        self.changing = Changing(self.forget)  # one change at a time; its end forgets the kept sections
        self._kept: dict[str, tuple[float, Any, dict[str, str]]] = {}
        self._uptime: int | None = None
        self.confirming = asyncio.Lock()
        self._token: ContextVar[str | None] = ContextVar("fritz_confirmation", default=None)
        # Connections kept open between requests (TR-064 and the web interface, by port).
        self.pool = Pool(host, self._check)

    async def http(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
        port: int | None = None,
        idempotent: bool | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """One request, without TR-064's authentication, over a connection whose certificate is
        the pinned one (the web interface on `port` 443 shows the same certificate as TR-064).
        `idempotent`: see pinned_http."""
        port = port or self.port
        if not path.startswith("/") or any(ord(c) <= 32 or ord(c) == 127 for c in path):
            raise ValueError("invalid TR-064 request path")
        if any("\r" in v or "\n" in v for v in headers.values()):
            raise ValueError("invalid TR-064 HTTP header")
        if self.transport is not None:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as c:
                r = await c.request(
                    method, f"https://{self.host}:{port}{path}", content=body, headers=headers
                )
                return r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.content
        try:
            return await self.pool.request(
                port, method, path, headers, body, timeout, MAX_RESPONSE, idempotent
            )
        except ValueError as e:
            if "too large" in str(e):
                raise ValueError("the FRITZ!Box response is too large") from e
            raise

    def _check(self, der: bytes) -> None:
        pin = self.pins.get(self.id)
        if pin is None:
            raise ChangedCertificate(f"{self.host}: no confirmed certificate (set its login up)")
        if fingerprint_of_der(der) != pin:
            raise ChangedCertificate(
                f"{self.host} shows another certificate than the one confirmed at its setup"
                " (confirm the new one if the FRITZ!Box made a new one)"
            )

    def forget(self) -> None:
        """What read() kept: read anew next time."""
        self._kept = {}

    def note_uptime(self, uptime: int | None) -> None:
        """The uptime DeviceInfo tells: lower than last time, the box restarted (a reboot, new
        FRITZ!OS), so what read() kept and the service list are old."""
        if uptime is None:
            return
        if self._uptime is not None and uptime < self._uptime:
            self.forget()
            self._services = None
        self._uptime = uptime

    async def request(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        idempotent: bool | None = None,
    ) -> tuple[int, bytes]:
        """One request with Digest authentication (a challenge is kept and reused)."""
        headers = dict(headers or {})
        for attempt in range(2):
            if self._challenge:
                self._nc += 1
                headers["Authorization"] = _digest(
                    self.user, self.password, method, path, self._challenge, self._nc
                )
            status, heads, payload = await self.http(
                method, path, headers, body, timeout, idempotent=idempotent
            )
            if status != 401:
                return status, payload
            challenge = heads.get("www-authenticate", "")
            stale = "stale=true" in challenge.lower().replace('"', "")
            if not challenge.lower().startswith("digest"):
                break
            fresh = self._challenge is None or stale or attempt == 0
            self._challenge, self._nc = challenge, 0
            if not fresh:
                break
        raise LoginRefused(f"{self.host} refused Kastellan's FRITZ!Box user or password")

    async def services(self) -> dict[str, str]:
        """serviceType -> controlURL, from the description (read once)."""
        if self._services is None:
            status, payload = await self.request("GET", "/tr64desc.xml")
            if status != 200:
                raise ConnectionError(f"{self.host}: no TR-064 description (HTTP {status})")
            root = ET.fromstring(payload)
            found: dict[str, str] = {}
            types: dict[str, str] = {}
            for svc in root.iter():
                if _local(svc.tag) != "service":
                    continue
                kids = {_local(c.tag): (c.text or "").strip() for c in svc}
                if kids.get("serviceType") and kids.get("controlURL"):
                    kind = kids["serviceType"].removeprefix(NS)
                    # Each WLAN instance has serviceType WLANConfiguration:1. The
                    # instance number belongs to serviceId, not the SOAP version.
                    key = kind
                    if kind.startswith("WLANConfiguration:"):
                        match = re.search(r":WLANConfiguration(\d+)$", kids.get("serviceId", ""))
                        if match is None:
                            continue
                        key = f"WLANConfiguration:{match[1]}"
                    found[key] = kids["controlURL"]
                    types[key] = kids["serviceType"]
            self._services, self._service_types = found, types
        return self._services

    async def call(
        self, service: str, action: str, args: dict[str, Any] | None = None, timeout: float = 30
    ) -> dict[str, str]:
        """One TR-064 action: its out arguments by name (strings). Its Get actions only read, so
        they may be sent again on a closed kept connection."""
        control = (await self.services()).get(service)
        if control is None:
            raise FritzError(401, f"this FRITZ!Box has no {service}")
        urn = self._service_types[service]
        inner = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in (args or {}).items())
        token = self._token.get()
        header = (
            '<s:Header><avm:token xmlns:avm="avm.de" s:mustUnderstand="1">'
            f"{escape(token)}</avm:token></s:Header>"
            if token
            else ""
        )
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
            ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f"{header}<s:Body>"
            f'<u:{action} xmlns:u="{urn}">{inner}</u:{action}></s:Body></s:Envelope>'
        ).encode()
        headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{urn}#{action}"'}
        reads = re.match(r"(X_AVM-DE_)?Get", action) is not None
        status, payload = await self.request("POST", control, body, headers, timeout, idempotent=reads)
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as e:
            raise FritzError(0, f"{action}: not a SOAP answer (HTTP {status})") from e
        for el in root.iter():
            if _local(el.tag) == "UPnPError":
                kids = {_local(c.tag): (c.text or "").strip() for c in el}
                code = int(kids.get("errorCode") or 0)
                raise FritzError(code, ERRORS.get(code) or f"error {code}")
        for el in root.iter():
            if _local(el.tag) == f"{action}Response":
                return {_local(c.tag): (c.text or "").strip() for c in el}
        raise FritzError(0, f"{action}: no answer (HTTP {status})")

    async def fetch(self, url: str, timeout: float = 60) -> bytes:
        """A file the FRITZ!Box names by URL (host lists, the export). Whatever host the URL
        names (it calls itself fritz.box), only its path is fetched, from this FRITZ!Box over the
        pinned connection."""
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        status, payload = await self.request("GET", path, timeout=timeout)
        if status != 200:
            raise ConnectionError(f"{self.host}: file download gave HTTP {status}")
        return payload


async def certificate(host: str, port: int, timeout: float = 10) -> str:
    """The fingerprint (SHA-256) of the certificate the FRITZ!Box shows now; nothing is sent."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with asyncio.timeout(timeout):
        _, writer = await asyncio.open_connection(host, port, ssl=ctx)
        try:
            return fingerprint_of_der(writer.get_extra_info("ssl_object").getpeercert(binary_form=True))
        finally:
            writer.close()


# --- Reading ----------------------------------------------------------------------------------


def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return str(v).strip() in ("1", "true", "True")


async def _try(coro) -> Any:
    try:
        return await coro
    except (FritzError, ConnectionError, TimeoutError, ET.ParseError):
        return None


# How long read() keeps a section (seconds): what rarely changes is asked less often than the
# line (every round). Each TR-064 action is a request the box answers on a slow CPU.
KEEP = {"update": 600, "forwards": 600, "remote": 600, "dhcp": 600, "mesh": 300, "wifi": 120, "hosts": 120}


async def _kept(fb: Fritz, name: str, errors: dict[str, str], make) -> Any:
    """A section of read(): kept for KEEP[name] seconds (and until a change of Kastellan's), with
    the errors it had."""
    cache = fb._kept
    kept = cache.get(name)
    if kept is not None and time.monotonic() - kept[0] < KEEP[name]:
        errors.update(kept[2])
        return kept[1]
    own: dict[str, str] = {}
    value = await make(own)
    cache[name] = (time.monotonic(), value, own)
    errors.update(own)
    return value


async def read(fb: Fritz) -> dict[str, Any]:
    """The FRITZ!Box's state, as the page shows it. What it doesn't offer (or doesn't allow) is
    left out; `errors` says what couldn't be read."""
    errors: dict[str, str] = {}
    info = await fb.call("DeviceInfo:1", "GetInfo")
    fb.note_uptime(_int(info.get("NewUpTime")))
    report: dict[str, Any] = {
        "model": info.get("NewModelName"),
        "firmware": info.get("NewSoftwareVersion"),
        "uptime": _int(info.get("NewUpTime")),
        "serial": None,  # never shown
        "errors": errors,
    }

    async def update(_e):
        ui = await _try(fb.call("UserInterface:1", "GetInfo"))
        if not ui:
            return None
        return {
            "available": _bool(ui.get("NewUpgradeAvailable")),
            "version": ui.get("NewX_AVM-DE_Version") or None,
        }

    async def wifi(e):
        networks = await _wifi(fb, e)
        for w in networks:
            w["associated"] = await _associated(fb, w["index"])
        return networks

    if (update_info := await _kept(fb, "update", errors, update)) is not None:
        report["update"] = update_info
    report["internet"] = await _internet(fb, errors)
    report["wifi"] = [dict(w) for w in await _kept(fb, "wifi", errors, wifi)]
    report["clients"] = await _kept(fb, "hosts", errors, lambda e: _hosts(fb, e))
    report["forwards"] = await _kept(fb, "forwards", errors, lambda e: _forwards(fb, e))
    report["remote"] = await _kept(fb, "remote", errors, lambda _e: _remote(fb))
    report["mesh"] = await _kept(fb, "mesh", errors, lambda e: _mesh(fb, e))
    dhcp_info = await _kept(
        fb, "dhcp", errors, lambda _e: _try(fb.call("LANHostConfigManagement:1", "GetInfo"))
    )
    report["dhcp"] = _dhcp(dhcp_info, report["clients"])
    return report


def _dhcp(info: dict[str, Any] | None, clients: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Its DHCP server (LANHostConfigManagement's GetInfo): on or off, the pool, and the active
    clients with an address in it."""
    if not info:
        return None
    low, high = info.get("NewMinAddress"), info.get("NewMaxAddress")
    size = None
    with contextlib.suppress(ValueError, TypeError):
        size = int(ipaddress.ip_address(high)) - int(ipaddress.ip_address(low)) + 1
    leases = [
        {"mac": c.get("mac"), "name": c.get("name"), "address": c.get("address"), "expires": None}
        for c in clients
        if c.get("address") and low and high and in_range(c["address"], low, high)
    ]
    return {
        "server": _bool(info.get("NewDHCPServerEnable")),
        "pools": [{"interface": "lan", "start": low, "end": high, "size": size}],
        "size": size,
        "used": len(leases),
        "leases": leases,
    }


def _rcpi(value: Any) -> int | None:
    """IEEE 802.11k's RCPI (0..220, 255 unknown) as dBm."""
    v = _int(value)
    return None if v is None or not 0 <= v <= 220 else round(v / 2 - 110)


async def _mesh(fb: Fritz, errors: dict[str, str]) -> dict[str, Any] | None:
    """FRITZ!Mesh: its members (master, repeaters) and how they are linked (Wi-Fi with signal,
    or cable), from the mesh list (Hosts: X_AVM-DE_GetMeshListPath, JSON)."""
    try:
        path = (await fb.call("Hosts:1", "X_AVM-DE_GetMeshListPath")).get("NewX_AVM-DE_MeshListPath")
        if not path:
            return None
        data = json.loads(await fb.fetch(path))
    except Exception as e:
        errors["mesh"] = (str(e) or type(e).__name__)[:120]
        return None
    nodes = [n for n in data.get("nodes") or [] if isinstance(n, dict)]
    meshed = {n.get("uid"): n for n in nodes if n.get("is_meshed")}
    members = [
        {
            "uid": n.get("uid"),
            "name": n.get("device_name"),
            "model": n.get("device_model"),
            "role": {"master": "master", "slave": "repeater"}.get(
                str(n.get("mesh_role")), n.get("mesh_role")
            ),
            "mac": str(n.get("device_mac_address") or "").lower() or None,
            "firmware": n.get("device_firmware_version"),
        }
        for n in meshed.values()
    ]
    links, seen = [], set()
    for n in meshed.values():
        for iface in n.get("node_interfaces") or []:
            for link in iface.get("node_links") or []:
                a, b = link.get("node_1_uid"), link.get("node_2_uid")
                if a not in meshed or b not in meshed or link.get("state") != "CONNECTED":
                    continue
                key = (link.get("uid"), tuple(sorted((str(a), str(b)))))
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "a": a,
                        "b": b,
                        "type": iface.get("type"),  # WLAN, LAN, PLC
                        "rate_rx": _int(link.get("cur_data_rate_rx")),  # kbit/s
                        "rate_tx": _int(link.get("cur_data_rate_tx")),
                        "signal": _rcpi(link.get("rx_rcpi")),
                    }
                )
    return {"members": members, "links": links}


async def _associated(fb: Fritz, index: int) -> list[dict[str, Any]]:
    """A Wi-Fi network's clients with signal (percent) and speed, from its device list."""
    try:
        path = (await fb.call(f"WLANConfiguration:{int(index)}", "X_AVM-DE_GetWLANDeviceListPath")).get(
            "NewX_AVM-DE_WLANDeviceListPath"
        )
        if not path:
            return []
        root = ET.fromstring(await fb.fetch(path))
    except Exception:
        return []
    out = []
    for item in root.iter():
        if _local(item.tag) != "Item":
            continue
        kids = {_local(c.tag): (c.text or "").strip() for c in item}
        out.append(
            {
                "mac": (kids.get("AssociatedDeviceMACAddress") or "").lower() or None,
                "address": kids.get("AssociatedDeviceIPAddress") or None,
                "signal": _int(kids.get("X_AVM-DE_SignalStrength")),  # percent
                "speed": _int(kids.get("X_AVM-DE_Speed")),  # Mbit/s
            }
        )
    return out[:200]


async def _internet(fb: Fritz, errors: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if link := await _try(fb.call("WANCommonInterfaceConfig:1", "GetCommonLinkProperties")):
        out |= {
            "access": link.get("NewWANAccessType"),  # DSL, Ethernet, X_AVM-DE_Fiber, X_AVM-DE_Cable
            "link": link.get("NewPhysicalLinkStatus"),  # Up, Down, Initializing
            "max_up": _int(link.get("NewLayer1UpstreamMaxBitRate")),
            "max_down": _int(link.get("NewLayer1DownstreamMaxBitRate")),
        }
    else:
        errors["internet"] = "the line's state can't be read"
    for svc in ("WANPPPConnection:1", "WANIPConnection:1"):
        c = await _try(fb.call(svc, "GetInfo"))
        if c and c.get("NewConnectionStatus"):
            out |= {
                "connection": c.get("NewConnectionStatus"),  # Connected, Connecting, Disconnected
                "connected_for": _int(c.get("NewUptime")),
                "address": c.get("NewExternalIPAddress") or None,
                "last_error": c.get("NewLastConnectionError") or None,
                "via": svc.split(":")[0],
            }
            break
    if (v6 := await _try(fb.call("WANIPConnection:1", "X_AVM-DE_GetExternalIPv6Address"))) and v6.get(
        "NewExternalIPv6Address"
    ):
        out["address_v6"] = v6["NewExternalIPv6Address"]
    if dsl := await _try(fb.call("WANDSLInterfaceConfig:1", "GetInfo")):
        out["dsl"] = {
            "status": dsl.get("NewStatus"),
            "up": _int(dsl.get("NewUpstreamCurrRate")),  # kbit/s
            "down": _int(dsl.get("NewDownstreamCurrRate")),
            "max_up": _int(dsl.get("NewUpstreamMaxRate")),
            "max_down": _int(dsl.get("NewDownstreamMaxRate")),
            "snr_up": _int(dsl.get("NewUpstreamNoiseMargin")),  # 0.1 dB
            "snr_down": _int(dsl.get("NewDownstreamNoiseMargin")),
        }
        if stats := await _try(fb.call("WANDSLInterfaceConfig:1", "GetStatisticsTotal")):
            out["dsl"]["crc"] = _int(stats.get("NewCRCErrors"))
            out["dsl"]["fec"] = _int(stats.get("NewFECErrors"))
            out["dsl"]["errored_seconds"] = _int(stats.get("NewErroredSecs"))
    out["rate"] = await rate(fb)
    return out


async def rate(fb: Fritz) -> dict[str, Any] | None:
    """What goes through the line now (bit/s), from the FRITZ!Box's online monitor; or its byte
    counters (the page takes the difference)."""
    if mon := await _try(
        fb.call("WANCommonInterfaceConfig:1", "X_AVM-DE_GetOnlineMonitor", {"NewSyncGroupIndex": 0})
    ):

        def first(key: str) -> int | None:
            return _int(str(mon.get(key) or "").split(",")[0])

        down, up = first("Newds_current_bps"), first("Newus_current_bps")
        if down is not None and up is not None:
            # bytes per second in the monitor: bits for everything else here
            return {"down": down * 8, "up": up * 8, "at": time.time()}
    sent = await _try(fb.call("WANCommonInterfaceConfig:1", "GetTotalBytesSent"))
    got = await _try(fb.call("WANCommonInterfaceConfig:1", "GetTotalBytesReceived"))
    if sent and got:
        return {
            "tx": _int(sent.get("NewTotalBytesSent")),
            "rx": _int(got.get("NewTotalBytesReceived")),
            "at": time.time(),
        }
    return None


async def _wifi(fb: Fritz, errors: dict[str, str]) -> list[dict[str, Any]]:
    """Each WLANConfiguration: a network on a band (the guest network is one of them)."""
    services = await fb.services()
    numbers = sorted(int(s.split(":")[1]) for s in services if s.startswith("WLANConfiguration:"))
    out = []
    for n in numbers:
        svc = f"WLANConfiguration:{n}"
        info = await _try(fb.call(svc, "GetInfo"))
        if info is None:
            errors[f"wifi{n}"] = "can't be read"
            continue
        ext = await _try(fb.call(svc, "X_AVM-DE_GetWLANExtInfo")) or {}
        clients = await _try(fb.call(svc, "GetTotalAssociations")) or {}
        channels = await _try(fb.call(svc, "GetChannelInfo")) or {}
        band = (
            info.get("NewX_AVM-DE_FrequencyBand")
            or channels.get("NewX_AVM-DE_FrequencyBand")
            or ext.get("NewX_AVM-DE_FrequencyBand")
            or ("2400" if n == 1 else None)
        )
        out.append(
            {
                "index": n,
                "ssid": info.get("NewSSID"),
                "enabled": _bool(info.get("NewEnable")),
                "status": info.get("NewStatus"),
                "channel": _int(info.get("NewChannel")),
                "possible_channels": [
                    _int(c) for c in str(channels.get("NewPossibleChannels") or "").split(",") if _int(c)
                ],
                "standard": info.get("NewStandard"),
                "encryption": info.get("NewBeaconType"),  # 11i (WPA2), WPAand11i, 11iandWPA3, WPA3, None
                "band": "2g"
                if str(band).startswith("24")
                else "5g"
                if str(band).startswith("5")
                else "6g"
                if str(band).startswith("6")
                else None,
                "guest": (ext.get("NewX_AVM-DE_APType") or "").lower() == "guest",
                "clients": _int(clients.get("NewTotalAssociations")) or 0,
                "bssid": (info.get("NewBSSID") or "").lower() or None,
            }
        )
    if (wps := await _try(fb.call("WLANConfiguration:1", "X_AVM-DE_GetWPSInfo"))) and out:
        out[0]["wps"] = (wps.get("NewX_AVM-DE_WPSStatus") or "").lower() not in ("off", "")
    return out


async def _hosts(fb: Fritz, errors: dict[str, str]) -> list[dict[str, Any]]:
    """The clients it knows, active ones: name, addresses, how they are connected."""
    try:
        path = (await fb.call("Hosts:1", "X_AVM-DE_GetHostListPath")).get("NewX_AVM-DE_HostListPath")
        if not path:
            return []
        root = ET.fromstring(await fb.fetch(path))
    except Exception as e:
        errors["clients"] = (str(e) or type(e).__name__)[:120]
        return []
    out = []
    for item in root.iter():
        if _local(item.tag) != "Item":
            continue
        kids = {_local(c.tag): (c.text or "").strip() for c in item}
        if not _bool(kids.get("Active")):
            continue
        out.append(
            {
                "name": kids.get("HostName") or None,
                "mac": (kids.get("MACAddress") or "").lower() or None,
                "address": kids.get("IPAddress") or None,
                "via": kids.get("InterfaceType") or None,  # Ethernet, 802.11
                "speed": _int(kids.get("X_AVM-DE_Speed")),  # Mbit/s
                "guest": _bool(kids.get("X_AVM-DE_Guest")),
            }
        )
    return out[:500]


async def _forwards(fb: Fritz, errors: dict[str, str]) -> list[dict[str, Any]] | None:
    """Port forwards to devices in the home network (security-relevant)."""
    for svc in ("WANPPPConnection:1", "WANIPConnection:1"):
        n = await _try(fb.call(svc, "GetPortMappingNumberOfEntries"))
        if n is None:
            continue
        out = []
        for i in range(min(_int(n.get("NewPortMappingNumberOfEntries")) or 0, 100)):
            e = await _try(fb.call(svc, "GetGenericPortMappingEntry", {"NewPortMappingIndex": i}))
            if not e:
                continue
            out.append(
                {
                    "enabled": _bool(e.get("NewPortMappingEnabled")),
                    "protocol": e.get("NewProtocol"),
                    "port": _int(e.get("NewExternalPort")),
                    "to": e.get("NewInternalClient"),
                    "to_port": _int(e.get("NewInternalPort")),
                    "name": e.get("NewPortMappingDescription") or None,
                }
            )
        return out
    errors["forwards"] = "can't be read"
    return None


async def _remote(fb: Fritz) -> dict[str, Any]:
    """Ways into the FRITZ!Box from the internet: remote access (HTTPS) and MyFRITZ!."""
    out: dict[str, Any] = {}
    if r := await _try(fb.call("X_AVM-DE_RemoteAccess:1", "GetInfo")):
        out["remote_access"] = _bool(r.get("NewEnabled"))
        out["remote_port"] = _int(r.get("NewPort"))
    if m := await _try(fb.call("X_AVM-DE_MyFritz:1", "GetInfo")):
        out["myfritz"] = _bool(m.get("NewEnabled"))
    return out


# --- The event log ----------------------------------------------------------------------------

LOG_LINE = re.compile(r"^(\d\d)\.(\d\d)\.(\d\d) (\d\d):(\d\d):(\d\d) (.*)$")


async def device_log(fb: Fritz) -> str:
    return (await fb.call("DeviceInfo:1", "GetDeviceLog")).get("NewDeviceLog", "")


def log_lines(
    device: str, text: str, after: dict[str, Any] | None
) -> tuple[list[Line], dict[str, Any] | None]:
    """The event log's lines ("21.11.26 10:03:12 message", newest first, the box's local time)
    that come after the cursor (the newest time and the messages at it)."""
    entries = []
    for raw in text.splitlines():
        m = LOG_LINE.match(raw.strip())
        if not m:
            continue
        d, mo, y, h, mi, s, msg = m.groups()
        try:
            t = datetime(2000 + int(y), int(mo), int(d), int(h), int(mi), int(s)).astimezone().timestamp()
        except ValueError:
            continue
        entries.append((t, msg.strip()))
    entries.reverse()  # oldest first
    last = float((after or {}).get("t", 0))
    seen = set((after or {}).get("msgs", []))
    new, newest, at_newest = [], last, set(seen)
    for t, msg in entries:
        if t < last or (t == last and msg in seen):
            continue
        level = (
            4 if re.search(r"(?i)fehl|fail|error|abgelehnt|refused|ungültig|invalid|unterbrochen", msg) else 6
        )
        new.append(Line(t, device, "fritzbox", "fritzbox", level, msg))
        if t > newest:
            newest, at_newest = t, {msg}
        elif t == newest:
            at_newest.add(msg)
    if not new:
        return [], after
    return new, {"t": newest, "msgs": sorted(at_newest)}


# --- Acting -----------------------------------------------------------------------------------


async def reboot(fb: Fritz) -> None:
    async with fb.changing:
        await fb.call("DeviceConfig:1", "Reboot")


async def update_firmware(fb: Fritz) -> None:
    """Install the update FRITZ!OS offers (it restarts with it, a few minutes)."""
    async with fb.changing:
        await fb.call("UserInterface:1", "X_AVM-DE_DoUpdate", timeout=60)


async def confirmed(fb: Fritz, call, wait: float = 90, say=None, poll: float = 2):
    """`call()` (an action FRITZ!OS may want confirmed on the box), and when it asks for its
    second factor (866): start the confirmation (X_AVM-DE_Auth), wait up to `wait` seconds for
    someone to press a button on the box (or dial the code it names), then call again."""
    async with fb.confirming:
        try:
            return await call()
        except FritzError as e:
            if e.code != 866 or wait <= 0:
                raise
        started = await fb.call("X_AVM-DE_Auth:1", "SetConfig", {"NewAction": "start"})
        token = started.get("NewToken")
        if not token:
            raise FritzError(866, "the FRITZ!Box gave no confirmation token")
        # AVM requires the token on every SOAP action in this process. A task-local value
        # keeps a concurrent poll or unrelated change from borrowing the confirmation.
        context = fb._token.set(token)
        try:
            if say:
                say(f"Confirm on the FRITZ!Box now: {started.get('NewMethods') or 'button'}")
            try:  # only the waiting is bounded by `wait`, not the action after it
                async with asyncio.timeout(wait):
                    state = started.get("NewState")
                    while state == "waitingforauth":
                        await asyncio.sleep(poll)
                        state = (await fb.call("X_AVM-DE_Auth:1", "GetState")).get("NewState")
            except TimeoutError as e:
                raise FritzError(866, "nobody confirmed it on the FRITZ!Box in time") from e
            if state not in ("authenticated", "disabled"):
                raise FritzError(
                    868 if state == "anotherauthprocess" else 867,
                    f"the confirmation on the FRITZ!Box ended ({state})",
                )
            return await call()
        finally:
            try:
                with contextlib.suppress(Exception):
                    await fb.call("X_AVM-DE_Auth:1", "SetConfig", {"NewAction": "stop"}, timeout=5)
            finally:
                fb._token.reset(context)


async def config_export(fb: Fritz, password: str) -> bytes:
    """The settings export, its passwords encrypted with `password` (needed to restore it)."""
    res = await fb.call(
        "DeviceConfig:1", "X_AVM-DE_GetConfigFile", {"NewX_AVM-DE_Password": password}, timeout=60
    )
    url = res.get("NewX_AVM-DE_ConfigFileUrl")
    if not url:
        raise FritzError(0, "the FRITZ!Box named no export file")
    data = await fb.fetch(url, timeout=120)
    if b"CONFIGURATION EXPORT" not in data[:500]:
        raise ValueError("the FRITZ!Box gave no settings export")
    return data  # The checksum covers the original bytes; never transcode an export.


def check_ssid(v: str) -> str:
    if not 1 <= len(v.encode()) <= 32 or any(ord(c) < 32 for c in v):
        raise ValueError("a network name is 1 to 32 bytes, no control characters")
    return v


def check_key(v: str) -> str:
    if not 8 <= len(v) <= 63 or any(ord(c) < 32 or ord(c) > 126 for c in v):
        raise ValueError("a Wi-Fi password is 8 to 63 printable ASCII characters")
    return v


# Kastellan's encryption words (OpenWrt's) to a FRITZ!Box's beacon types.
BEACON = {"sae-mixed": "11iandWPA3", "sae": "WPA3", "psk2": "11i", "psk-mixed": "WPAand11i"}


async def change_wifi(fb: Fritz, index: int, values: dict[str, Any]) -> None:
    """Name, password, encryption, hidden, on/off, channel (0: automatic) of one
    WLANConfiguration."""
    svc = f"WLANConfiguration:{int(index)}"
    # The whole request checked before the first change.
    ssid = check_ssid(str(values["ssid"])) if values.get("ssid") is not None else None
    key = check_key(str(values["key"])) if values.get("key") is not None else None
    channel = int(values["channel"]) if values.get("channel") is not None else None
    if channel is not None and not 0 <= channel <= 233:
        raise ValueError("not a channel")
    if values.get("encryption") and values["encryption"] not in BEACON:
        raise ValueError("a FRITZ!Box takes WPA2, WPA3, WPA2/WPA3 or WPA/WPA2")
    async with fb.changing:
        if values.get("encryption"):
            await fb.call(svc, "SetBeaconType", {"NewBeaconType": BEACON[values["encryption"]]})
        if values.get("hidden") is not None:
            await fb.call(
                svc, "SetBeaconAdvertisement", {"NewBeaconAdvertisementEnabled": 0 if values["hidden"] else 1}
            )
        if ssid:
            await fb.call(svc, "SetSSID", {"NewSSID": ssid})
        if key:
            await fb.call(
                svc,
                "SetSecurityKeys",
                {
                    "NewWEPKey0": "",
                    "NewWEPKey1": "",
                    "NewWEPKey2": "",
                    "NewWEPKey3": "",
                    "NewPreSharedKey": "",
                    "NewKeyPassphrase": key,
                },
            )
        if channel is not None:
            await fb.call(svc, "SetChannel", {"NewChannel": channel})
        if values.get("enabled") is not None:
            await fb.call(svc, "SetEnable", {"NewEnable": 1 if values["enabled"] else 0})


async def wifi_key(fb: Fritz, index: int) -> str | None:
    """A network's password (only to compare it by fingerprint, never shown)."""
    keys = await _try(fb.call(f"WLANConfiguration:{int(index)}", "GetSecurityKeys"))
    return (keys or {}).get("NewKeyPassphrase") or None


async def wps_off(fb: Fritz) -> None:
    async with fb.changing:
        for svc in [s for s in await fb.services() if s.startswith("WLANConfiguration:")]:
            await fb.call(svc, "X_AVM-DE_SetWPSEnable", {"NewX_AVM-DE_WPSEnable": 0})
