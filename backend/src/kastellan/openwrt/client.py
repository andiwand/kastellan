"""Talking to an OpenWrt device: ubus over HTTPS (/ubus, JSON-RPC), as LuCI does, with
Kastellan's own login (setup.py), and what is read and done with it.

The device's certificate is pinned by its fingerprint: the setup reads it on the device (over
the SSH connection whose host key is checked) and the hub compares every connection's
certificate with it before a byte is sent. Validity dates don't count (uhttpd's own certificate
is never renewed, and a router's clock may be wrong); only the certificate itself. A device
that shows another one gets nothing until its login is set up again.

HTTP/1.1 connections are reused briefly between calls.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import re
import secrets
import ssl
import time
from pathlib import Path
from typing import Any

import httpx

from ..pinned_http import Pool

NULL_SESSION = "0" * 32
DENIED = -32002  # uhttpd: the session may not call that (or it has ended)
MAX_RESPONSE = 8 * 1024 * 1024
CONFIRM_AFTER = 5.0  # seconds between applying a Wi-Fi change and looking at the radios


class ChangedCertificate(Exception):
    pass


class LoginRefused(Exception):
    pass


class UbusError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ubus status codes (libubus) and uhttpd's JSON-RPC errors: what a failed call means.
STATUS = {
    1: "invalid command",
    2: "invalid argument",
    3: "method not found",
    4: "not found",
    5: "no data",
    6: "not allowed",
    7: "timeout",
    8: "not supported",
    9: "unknown error",
    10: "connection failed",
    -32000: "not there (its package isn't installed)",
    -32002: "not allowed (its login doesn't have that ability)",
    -32003: "it took too long",
    -32601: "no such method",
    -32700: "the request was refused (too large?)",
}


def fingerprint_of_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fingerprint_from_setup(lines: list[str]) -> str | None:
    """The certificate's fingerprint as the setup printed it: `KASTELLAN-CERT der <sha256>`
    (uhttpd's own, a DER file), or `KASTELLAN-CERT pem <line>` lines (a PEM file; its first
    certificate is the one uhttpd shows)."""
    pem: list[str] = []
    for line in lines:
        if line.startswith("KASTELLAN-CERT der "):
            fp = line.split()[2].lower()
            return fp if re.fullmatch(r"[0-9a-f]{64}", fp) else None
        if line.startswith("KASTELLAN-CERT pem "):
            pem.append(line[len("KASTELLAN-CERT pem ") :])
    text = "\n".join(pem)
    m = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", text, re.S)
    if not m:
        return None
    try:
        return fingerprint_of_der(ssl.PEM_cert_to_DER_cert(m.group(0)))
    except ValueError:
        return None


class Pins:
    """Each device's pinned certificate fingerprint: <data>/openwrt/<id>.sha256."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def get(self, id: str) -> str | None:
        try:
            return (self.folder / f"{id}.sha256").read_text().strip() or None
        except OSError:
            return None

    def set(self, id: str, fp: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{id}.sha256").write_text(fp + "\n")

    def forget(self, id: str) -> None:
        (self.folder / f"{id}.sha256").unlink(missing_ok=True)


SLOW_EVERY = 600.0  # seconds: how long `Ubus.slow` keeps a read


class _Changing:
    """`Ubus.changing`: a lock whose end forgets the kept reads (what it changed shows next)."""

    def __init__(self, ubus: Ubus) -> None:
        self._lock, self._ubus = asyncio.Lock(), ubus

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self) -> None:
        await self._lock.acquire()

    async def __aexit__(self, *exc: object) -> None:
        self._ubus.forget()
        self._lock.release()


class Ubus:
    """One device's ubus, with Kastellan's login; the session is kept and renewed."""

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
        self.session: str | None = None
        self.expires = 0.0
        self._id = 0
        self._login_lock = asyncio.Lock()
        # One change (Wi-Fi, firmware, services) at a time; it ends what `slow` kept.
        self.changing = _Changing(self)
        self._slow: dict[str, tuple[float, Any]] = {}
        # Connections kept open between calls: one TLS handshake per round, not per call.
        self.pool = Pool(host, self._check)

    async def _http(self, path: str, body: bytes, content_type: str, timeout: float) -> tuple[int, bytes]:
        if self.transport is not None:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as c:
                r = await c.post(
                    f"https://{self.host}:{self.port}{path}",
                    content=body,
                    headers={"content-type": content_type},
                )
                return r.status_code, r.content
        _status, _heads, payload = await self.pool.request(
            self.port, "POST", path, {"Content-Type": content_type}, body, timeout, MAX_RESPONSE
        )
        return _status, payload

    def _check(self, der: bytes) -> None:
        pin = self.pins.get(self.id)
        if pin is None:
            raise ChangedCertificate(f"{self.host}: no pinned certificate (set its login up)")
        if fingerprint_of_der(der) != pin:
            raise ChangedCertificate(
                f"{self.host} shows another certificate than at its setup"
                " (set its login up again if it was reset)"
            )

    async def _rpc(self, session: str, obj: str, method: str, args: dict[str, Any], timeout: float) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": "call", "params": [session, obj, method, args]}
        status, payload = await self._http("/ubus", json.dumps(body).encode(), "application/json", timeout)
        try:
            data = json.loads(payload)
        except ValueError as e:
            raise UbusError(-32700, f"HTTP {status}: not a JSON answer") from e
        if "error" in data:
            code = int(data["error"].get("code", -32603))
            raise UbusError(code, STATUS.get(code, str(data["error"].get("message", "error"))))
        result = data.get("result") or [9]
        if result[0] != 0:
            raise UbusError(result[0], STATUS.get(result[0], f"status {result[0]}"))
        if session != NULL_SESSION:
            self.expires = time.monotonic() + self._timeout - 30  # rpcd extends it on every use
        return result[1] if len(result) > 1 else {}

    _timeout = 300.0

    async def session_id(self, renew_from: str | None = None, min_left: float = 0) -> str:
        """A live session: the kept one, or a new login (once, even with callers at the same time)."""
        async with self._login_lock:
            stale = self.session is None or time.monotonic() > self.expires - min_left
            if stale or (renew_from is not None and self.session == renew_from):
                try:
                    res = await self._rpc(
                        NULL_SESSION,
                        "session",
                        "login",
                        {"username": self.user, "password": self.password},
                        15,
                    )
                except UbusError as e:
                    if e.code in (6, DENIED):
                        raise LoginRefused(
                            f"{self.host} refused Kastellan's password (set its login up again)"
                        ) from e
                    raise
                self.session = res["ubus_rpc_session"]
                self._timeout = float(res.get("timeout") or res.get("expires") or 300)
                self.expires = time.monotonic() + self._timeout - 30
            assert self.session is not None
            return self.session

    async def slow(self, obj: str, method: str, args: dict[str, Any] | None = None, every: float = 0) -> Any:
        """A read of what rarely changes (the board, uci dhcp, the services): kept `every`
        seconds (SLOW_EVERY), and until a change of Kastellan's (`changing`) or a new client."""
        key = json.dumps([obj, method, args or {}], sort_keys=True)
        cache = self._slow
        kept = cache.get(key)
        if kept is not None and time.monotonic() - kept[0] < (every or SLOW_EVERY):
            return kept[1]
        result = await self.call(obj, method, args)
        cache[key] = (time.monotonic(), result)  # a concurrent forget() replaces this cache
        return result

    def forget(self) -> None:
        """What `slow` kept: read anew next time."""
        self._slow = {}

    async def call(
        self,
        obj: str,
        method: str,
        args: dict[str, Any] | None = None,
        timeout: float = 20,
        session: str | None = None,
    ) -> Any:
        """One call. With `session`, exactly in that session (staged changes live in it);
        otherwise in the kept one, logging in again once if it has ended."""
        if session is not None:
            return await self._rpc(session, obj, method, args or {}, timeout)
        s = await self.session_id()
        try:
            return await self._rpc(s, obj, method, args or {}, timeout)
        except UbusError as e:
            if e.code != DENIED:
                raise
        s = await self.session_id(renew_from=s)  # the session ended (a reboot, or it timed out)
        return await self._rpc(s, obj, method, args or {}, timeout)

    async def upload(self, path: str, data: bytes, session: str, timeout: float = 300) -> dict[str, Any]:
        """A file onto the device through cgi-io (LuCI's upload; checks the `file` write ACL)."""
        boundary = "kastellan" + secrets.token_hex(12)
        parts = []
        for name, value in (("sessionid", session), ("filename", path), ("filemode", "0600")):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="filedata"; filename="firmware.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + data
            + f"\r\n--{boundary}--\r\n".encode()
        )
        status, payload = await self._http(
            "/cgi-bin/cgi-upload", b"".join(parts), f"multipart/form-data; boundary={boundary}", timeout
        )
        if status == 403:
            raise UbusError(DENIED, STATUS[DENIED])
        if status != 200:
            raise ValueError(f"the upload was refused (HTTP {status}: {payload[:120].decode('latin-1')})")
        return json.loads(payload)


# --- Reading ----------------------------------------------------------------------------------


async def read(ubus: Ubus) -> dict[str, Any]:
    """The device's state, as the page shows it."""
    board = await ubus.slow("system", "board")
    info = await ubus.call("system", "info")
    report: dict[str, Any] = {
        "board": {
            "model": board.get("model"),
            "hostname": board.get("hostname"),
            "board_name": board.get("board_name"),
            "release": {
                k: (board.get("release") or {}).get(k)
                for k in ("version", "revision", "target", "description")
            },
            "kernel": board.get("kernel"),
            "rootfs_type": board.get("rootfs_type"),
        },
        "system": {
            "uptime": info.get("uptime"),
            "load": [x / 65536 for x in (info.get("load") or [])[:3]],
            "memory": {
                k: (info.get("memory") or {}).get(k) for k in ("total", "free", "available", "buffered")
            },
        },
        "errors": {},
    }
    dump = []
    try:
        dump = (await ubus.call("network.interface", "dump")).get("interface", [])
        report["interfaces"] = [
            {
                "name": i.get("interface"),
                "up": bool(i.get("up")),
                "proto": i.get("proto"),
                "device": i.get("l3_device") or i.get("device"),
                "ipv4": [a.get("address") for a in i.get("ipv4-address") or []][:4],
                "uptime": i.get("uptime"),
            }
            for i in dump
            if i.get("interface") != "loopback"
        ]
    except Exception as e:
        report["errors"]["interfaces"] = str(e)[:200]
    leases: dict[str, str] = {}
    lease_list: list[dict[str, Any]] | None = None
    with contextlib.suppress(Exception):
        raw_leases = (await ubus.call("luci-rpc", "getDHCPLeases"))["dhcp_leases"]
        lease_list = []
        for lease in raw_leases:
            mac = str(lease.get("macaddr") or "").lower()
            if mac and lease.get("hostname"):
                leases[mac] = lease["hostname"]
            if mac and lease.get("ipaddr"):
                lease_list.append(
                    {
                        "mac": mac,
                        "name": lease.get("hostname") or None,
                        "address": lease.get("ipaddr"),
                        "expires": lease.get("expires"),
                    }
                )
    report["dhcp"] = await _dhcp(ubus, lease_list, dump) if lease_list is not None else None
    try:
        try:
            status = await ubus.call("network.wireless", "status")
        except UbusError as e:
            if e.code != 2:  # not "invalid argument": a real refusal
                raise
            # OpenWrt 25.12 answers "invalid argument" here: the radios as iwinfo sees them.
            status = None
        if status is None:
            report["radios"] = await _radios_from_iwinfo(ubus, leases)
            status = {}
        radios = report.get("radios") or []
        for name, radio in status.items():
            cfg = radio.get("config") or {}
            ifaces = []
            for iface in radio.get("interfaces") or []:
                ic = iface.get("config") or {}
                ifname = iface.get("ifname")
                clients = []
                if ifname:
                    with contextlib.suppress(Exception):
                        for c in (await ubus.call("iwinfo", "assoclist", {"device": ifname})).get(
                            "results", []
                        ):
                            clients.append(assoc_client(c, leases))
                ifaces.append(
                    {
                        "section": iface.get("section"),
                        "ifname": ifname,
                        "ssid": ic.get("ssid") or ic.get("mesh_id"),
                        "mode": ic.get("mode"),
                        "network": ic.get("network"),
                        "clients": clients,
                    }
                )
            radios.append(
                {
                    "name": name,
                    "up": bool(radio.get("up")),
                    "disabled": bool(radio.get("disabled")),
                    "band": cfg.get("band") or HWMODE.get(str(cfg.get("hwmode") or ""), cfg.get("hwmode")),
                    "channel": cfg.get("channel"),
                    "htmode": cfg.get("htmode"),
                    "country": cfg.get("country"),
                    "interfaces": ifaces,
                }
            )
        report["radios"] = radios
    except Exception as e:
        report["errors"]["wifi"] = str(e)[:200]
    try:
        rc = await ubus.slow("rc", "list", every=300)
        report["services"] = [
            {"name": n, "enabled": bool(v.get("enabled")), "running": bool(v.get("running"))}
            for n, v in sorted(rc.items())
        ][:200]
    except Exception as e:  # rc arrived in OpenWrt 21.02
        report["errors"]["services"] = str(e)[:200]
    from .setup import SERVICES_ALLOW

    try:  # the services Kastellan may act on
        data = (await ubus.slow("file", "read", {"path": SERVICES_ALLOW})).get("data", "")
        report["services_allowed"] = [ln for ln in str(data).split("\n") if ln][:100]
    except Exception:
        report["services_allowed"] = []
    return report


# Before OpenWrt 21.02 radios said hwmode (11g, 11a) where later ones say band (2g, 5g).
HWMODE = {"11b": "2g", "11g": "2g", "11ng": "2g", "11a": "5g", "11na": "5g", "11ac": "5g", "11ax": "5g"}


def _band(info: dict[str, Any]) -> str | None:
    mhz = info.get("frequency")
    if not isinstance(mhz, int | float):
        return None
    return "2g" if mhz < 3000 else "5g" if mhz < 5950 else "6g"


async def _clients(ubus: Ubus, ifname: str, leases: dict[str, str]) -> list[dict[str, Any]]:
    clients = []
    with contextlib.suppress(Exception):
        for c in (await ubus.call("iwinfo", "assoclist", {"device": ifname})).get("results", []):
            clients.append(assoc_client(c, leases))
    return clients


async def _radios_from_iwinfo(ubus: Ubus, leases: dict[str, str]) -> list[dict[str, Any]]:
    """The radios and their interfaces from iwinfo alone (devices, info): grouped by phy, named
    radioN for phyN as OpenWrt does. Without uci's section names (the interface's name instead)."""
    devices = (await ubus.call("iwinfo", "devices")).get("devices") or []
    radios: dict[str, dict[str, Any]] = {}
    for ifname in devices:
        if not isinstance(ifname, str):
            continue
        try:
            info = await ubus.call("iwinfo", "info", {"device": ifname})
        except Exception:
            continue
        phy = str(info.get("phy") or ifname.split("-")[0])
        name = "radio" + phy.removeprefix("phy") if phy.startswith("phy") else phy
        radio = radios.setdefault(
            name,
            {
                "name": name,
                "up": True,
                "disabled": False,
                "band": _band(info),
                "channel": info.get("channel"),
                "htmode": info.get("htmode"),
                "country": info.get("country"),
                "interfaces": [],
            },
        )
        mode = str(info.get("mode") or "").lower()
        radio["interfaces"].append(
            {
                "section": ifname,
                "ifname": ifname,
                "ssid": info.get("ssid") or info.get("meshid"),
                "mode": "ap" if mode.startswith("master") else "mesh" if "mesh" in mode else mode or None,
                "network": None,
                "clients": await _clients(ubus, ifname, leases),
            }
        )
    return sorted(radios.values(), key=lambda r: r["name"])


async def _dhcp(ubus: Ubus, leases: list[dict[str, Any]], interfaces: list[dict[str, Any]]) -> dict[str, Any]:
    """The DHCP server: its pools (uci dhcp: start, limit per interface; the read group's uci
    scope, set up again on older logins) and the leases handed out."""
    pools: list[dict[str, Any]] | None = None
    ranges = []
    with contextlib.suppress(Exception):
        values = (await ubus.slow("uci", "get", {"config": "dhcp"})).get("values", {})
        pools = []
        for name, sec in values.items():
            if (
                sec.get(".type") != "dhcp"
                or str(sec.get("ignore", "0")) == "1"
                or sec.get("dhcpv4") == "disabled"
            ):
                continue
            with contextlib.suppress(TypeError, ValueError):
                interface = sec.get("interface") or name
                start, size = int(sec.get("start", 100)), int(sec.get("limit", 150))
                if start < 0 or size <= 0:
                    continue
                pools.append(
                    {
                        "interface": interface,
                        "start": start,
                        "size": size,
                        "leasetime": sec.get("leasetime"),
                    }
                )
                addresses = next(
                    (i.get("ipv4-address") or [] for i in interfaces if i.get("interface") == interface), []
                )
                if addresses and "mask" in addresses[0]:
                    a = addresses[0]
                    network = ipaddress.IPv4Network(f"{a['address']}/{a['mask']}", strict=False)
                    low = int(network.network_address) + start
                    high = low + size - 1
                    if int(network.network_address) < low <= high < int(network.broadcast_address):
                        ranges.append((low, high))
    # Static leases outside a dynamic pool don't consume it. Without the interface ranges,
    # report leases but leave capacity unknown instead of raising a false full-pool alarm.
    size = sum(p["size"] for p in pools) if pools and len(ranges) == len(pools) else None
    used = len(leases)
    if size is not None:
        addresses = set()
        for lease in leases:
            with contextlib.suppress(ValueError, TypeError):
                address = int(ipaddress.IPv4Address(lease.get("address")))
                if any(low <= address <= high for low, high in ranges):
                    addresses.add(address)
        used = len(addresses)
    return {"pools": pools, "size": size, "used": used, "leases": leases[:500]}


async def service(ubus: Ubus, name: str, action: str) -> str:
    """start/stop/restart/enable/disable one allowed service, through the one script setup
    installs (it checks the allow list on the device)."""
    from .setup import SERVICE

    async with ubus.changing:
        res = await ubus.call("file", "exec", {"command": SERVICE, "params": [name, action]}, timeout=55)
    if res.get("code", 0) != 0:
        raise ValueError(
            str(res.get("stderr") or res.get("stdout") or f"exit {res.get('code')}").strip()[:300]
        )
    return str(res.get("stdout", "")).strip()[:300]


async def upgradable(ubus: Ubus) -> list[str]:
    """Packages with updates ("name - old - new"), through the one fixed script setup installs."""
    from .setup import UPGRADABLE

    res = await ubus.call("file", "exec", {"command": UPGRADABLE, "params": []}, timeout=55)
    return [ln.strip() for ln in str(res.get("stdout", "")).splitlines() if ln.strip()][:200]


async def openvpn_status(ubus: Ubus) -> str:
    """Who is connected to its OpenVPN servers: the one fixed script's output (vpn/openvpn.py
    reads it). UbusError when the login hasn't that ability."""
    from .setup import OPENVPN_STATUS

    res = await ubus.call("file", "exec", {"command": OPENVPN_STATUS, "params": []}, timeout=20)
    return str(res.get("stdout", ""))[:1048576]


async def bandwidth(ubus: Ubus) -> dict[str, dict[str, int]]:
    """Bytes per device (MAC) this accounting period, from nlbwmon (the `bandwidth` ability):
    rx is what the device received (its download), tx what it sent. ValueError when nlbwmon
    isn't there."""
    from .setup import BANDWIDTH

    res = await ubus.call("file", "exec", {"command": BANDWIDTH, "params": []}, timeout=20)
    return parse_nlbw(str(res.get("stdout", ""))[:4_000_000])


def parse_nlbw(text: str) -> dict[str, dict[str, int]]:
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ValueError("nlbwmon gave nothing readable") from e
    if not isinstance(data, dict):
        raise ValueError("nlbwmon gave nothing readable")
    if data.get("error"):
        raise ValueError(str(data["error"])[:200])
    columns = data.get("columns") or []
    try:
        mac, rx, tx = columns.index("mac"), columns.index("rx_bytes"), columns.index("tx_bytes")
    except ValueError as e:
        raise ValueError("nlbwmon's columns are not as expected") from e
    out: dict[str, dict[str, int]] = {}
    for row in (data.get("data") or [])[:5000]:
        if not isinstance(row, list) or len(row) <= max(mac, rx, tx):
            continue
        m = str(row[mac]).lower()
        if not re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", m):
            continue
        try:
            got = out.setdefault(m, {"rx": 0, "tx": 0})
            got["rx"] += int(row[rx])
            got["tx"] += int(row[tx])
        except (TypeError, ValueError):
            continue
    return out


ROAMING_CONFIG = (
    "min_snr",
    "min_connect_snr",
    "roam_scan_snr",
    "roam_trigger_snr",
    "signal_diff_threshold",
    "band_steering_threshold",
    "load_kick_enabled",
    "assoc_steering",
    "probe_steering",
)


async def roaming(ubus: Ubus) -> dict[str, Any]:
    """What usteer on this access point knows (the `roaming` ability): its own nodes (one per
    radio's network) and the other access points' it hears of, the clients connected here, and
    a part of its settings. Raises when usteer isn't running."""
    local = await ubus.call("usteer", "local_info", {}, timeout=10)
    remote = await ubus.call("usteer", "remote_info", {}, timeout=10)
    connected: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        connected = await ubus.call("usteer", "connected_clients", {}, timeout=10)
    config: dict[str, Any] = {}
    with contextlib.suppress(Exception):
        config = await ubus.call("usteer", "get_config", {}, timeout=10)
    return parse_roaming(local, remote, connected, config)


def parse_roaming(local: Any, remote: Any, connected: Any, config: Any) -> dict[str, Any]:
    def node(name: str, info: Any, own: bool) -> dict[str, Any] | None:
        if not isinstance(info, dict):
            return None
        roam = info.get("roam_events") if isinstance(info.get("roam_events"), dict) else {}
        host, _, iface = name.rpartition("#")
        return {
            "node": name[:120],
            "own": own,
            "host": host or None,
            "iface": iface.removeprefix("hostapd.")[:40],
            "bssid": str(info.get("bssid") or "").lower()[:17] or None,
            "ssid": str(info.get("ssid") or "")[:64] or None,
            "freq": info.get("freq") if isinstance(info.get("freq"), int) else None,
            "clients": info.get("n_assoc") if isinstance(info.get("n_assoc"), int) else None,
            "load": info.get("load") if isinstance(info.get("load"), int) else None,
            "roam_source": roam.get("source") if isinstance(roam.get("source"), int) else None,
            "roam_target": roam.get("target") if isinstance(roam.get("target"), int) else None,
        }

    nodes = [
        n
        for own, part in ((True, local), (False, remote))
        if isinstance(part, dict)
        for name, info in list(part.items())[:64]
        if (n := node(str(name), info, own))
    ]
    clients = []
    for name, macs in connected.items() if isinstance(connected, dict) else []:
        if not isinstance(macs, dict):
            continue
        for mac, c in list(macs.items())[:512]:
            if not isinstance(c, dict) or not re.fullmatch(r"[0-9a-f:]{17}", str(mac).lower()):
                continue
            clients.append(
                {
                    "mac": str(mac).lower(),
                    "node": str(name)[:120],
                    "signal": c.get("signal") if isinstance(c.get("signal"), int) else None,
                    "snr": c.get("snr") if isinstance(c.get("snr"), int) else None,
                    "connected": c.get("connected_time")
                    if isinstance(c.get("connected_time"), int)
                    else None,
                }
            )
    settings = {
        k: config.get(k)
        for k in ROAMING_CONFIG
        if isinstance(config, dict) and isinstance(config.get(k), int | bool)
    }
    return {"nodes": nodes, "clients": clients, "config": settings}


def assoc_client(c: dict[str, Any], leases: dict[str, str]) -> dict[str, Any]:
    """A Wi-Fi client as iwinfo's assoclist tells it: signal and noise (dBm), the rates both ways
    (Mbit/s) with their width and standard, and what it may expect to get through."""
    mac = str(c.get("mac") or "").lower()

    def num(v: Any) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    def rate(d: Any) -> dict[str, Any] | None:
        if not isinstance(d, dict) or num(d.get("rate")) is None:
            return None
        std = "he" if d.get("he") else "vht" if d.get("vht") else "ht" if d.get("ht") else None
        return {
            "mbit": round(d["rate"] / 1000, 1),  # iwinfo: kbit/s
            "mhz": num(d.get("mhz")) or (40 if d.get("40mhz") else None),
            "mcs": num(d.get("mcs")),
            "nss": num(d.get("nss")),
            "std": std,
        }

    signal, noise = num(c.get("signal")), num(c.get("noise"))
    expected = num(c.get("expected_throughput"))
    return {
        "mac": mac,
        "name": leases.get(mac),
        "signal": signal,
        "inactive": c.get("inactive"),
        "snr": signal - noise if signal is not None and noise not in (None, 0) else None,
        "rx": rate(c.get("rx")),  # from the client
        "tx": rate(c.get("tx")),  # to the client
        "expected": round(expected / 1000, 1) if expected else None,  # Mbit/s
        "connected": num(c.get("connected_time")),
    }


async def reboot(ubus: Ubus) -> None:
    await ubus.call("system", "reboot")


async def read_log(ubus: Ubus, lines: int = 1000) -> list[dict[str, Any]]:
    """The newest `lines` of logd's ring buffer (the `logs` ability): each with msg, priority,
    source (0 kernel, 1 syslog), time (ms) and id. Oldest first."""
    res = await ubus.call("log", "read", {"lines": lines, "stream": False, "oneshot": True}, timeout=30)
    return [e for e in (res.get("log") or []) if isinstance(e, dict)]


async def backup(ubus: Ubus) -> bytes:
    """The configuration backup, as LuCI downloads it (`sysupgrade -b`, a .tar.gz; the `backup`
    ability). Holds every password and key on the device."""
    s = await ubus.session_id(min_left=60)
    status, payload = await ubus._http(
        "/cgi-bin/cgi-backup", f"sessionid={s}".encode(), "application/x-www-form-urlencoded", 120
    )
    if status == 403:
        raise UbusError(DENIED, STATUS[DENIED])
    if status != 200 or payload[:2] != b"\x1f\x8b":
        raise ValueError(f"the device gave no backup (HTTP {status})")
    return payload


async def iface_info(ubus: Ubus, ifname: str) -> dict[str, Any]:
    """A Wi-Fi interface's own address (bssid) and channel, as iwinfo tells it."""
    res = await ubus.call("iwinfo", "info", {"device": ifname}, timeout=10)
    return {"bssid": str(res.get("bssid") or "").lower() or None, "channel": res.get("channel")}


async def wan_counters(ubus: Ubus, device: str) -> dict[str, int] | None:
    """The byte counters of the network device behind the WAN (read: network.device status)."""
    try:
        res = await ubus.call("network.device", "status", {"name": device}, timeout=10)
    except Exception:
        return None
    stats = res.get("statistics") or {}
    if "rx_bytes" not in stats:
        return None
    return {"rx": int(stats["rx_bytes"]), "tx": int(stats["tx_bytes"])}


async def scan(ubus: Ubus, ifname: str) -> list[dict[str, Any]]:
    """Neighbouring networks a radio hears (the `scan` ability; clients may pause a moment)."""
    res = await ubus.call("iwinfo", "scan", {"device": ifname}, timeout=40)
    out = []
    for r in res.get("results") or []:
        enc = r.get("encryption") or {}
        out.append(
            {
                "ssid": r.get("ssid"),
                "bssid": str(r.get("bssid") or "").lower(),
                "channel": r.get("channel"),
                "mhz": r.get("mhz"),
                "band": r.get("band"),
                "signal": r.get("signal"),
                "width": _width(r),
                "encrypted": bool(enc.get("enabled")),
            }
        )
    return out


def _width(r: dict[str, Any]) -> int | None:
    """The channel width a scanned network uses (MHz), from what it announces."""
    for key, field in (
        ("eht_operation", "channel_width"),
        ("he_operation", "channel_width"),
        ("vht_operation", "channel_width"),
        ("ht_operation", "secondary_channel_offset"),
    ):
        op = r.get(key)
        if not isinstance(op, dict):
            continue
        value = op.get(field)
        if key == "ht_operation":
            return 40 if value and value != "no secondary" else 20
        if isinstance(value, int) and value > 0:
            return 20 if value < 20 else value
    return None


async def survey(ubus: Ubus, ifname: str) -> list[dict[str, Any]]:
    """How busy each channel was (iwinfo survey, the `scan` ability): per frequency the time
    listened (active) and the time it was busy, in ms, and the noise. Older devices lack it."""
    res = await ubus.call("iwinfo", "survey", {"device": ifname}, timeout=20)
    out = []
    for r in res.get("results") or []:
        active, busy = r.get("active_time"), r.get("busy_time")
        out.append(
            {
                "mhz": r.get("mhz"),
                "noise": r.get("noise"),
                "busy": round(100 * busy / active) if active and busy is not None else None,
                "in_use": bool(r.get("in_use")),
            }
        )
    return out


async def freqlist(ubus: Ubus, ifname: str) -> list[dict[str, Any]]:
    """The channels a radio may use here: channel, MHz, and whether it is restricted (DFS: it
    must listen for radar first and may have to move)."""
    res = await ubus.call("iwinfo", "freqlist", {"device": ifname}, timeout=15)
    return [
        {
            "channel": r.get("channel"),
            "mhz": r.get("mhz"),
            "restricted": bool(r.get("restricted")),
            "active": bool(r.get("active")),
        }
        for r in res.get("results") or []
    ]


# --- Wi-Fi ------------------------------------------------------------------------------------

# What the page may change, per kind of section; nothing else in `wireless` is touched.
RADIO_FIELDS = ("channel", "htmode", "txpower", "disabled", "country")
IFACE_FIELDS = (
    "ssid",
    "key",
    "encryption",
    "disabled",
    "hidden",
    "mesh_id",
    # roaming (roaming.py)
    "ieee80211r",
    "mobility_domain",
    "ft_over_ds",
    "ft_psk_generate_local",
    "ieee80211k",
    "bss_transition",
)
ENCRYPTIONS = {
    "none",
    "owe",
    "psk",
    "psk2",
    "psk-mixed",
    "psk2+ccmp",
    "sae",
    "sae-mixed",
    "wpa2",
    "wpa3",
    "wpa3-mixed",
}
HTMODES = re.compile(r"^(NOHT|HT20|HT40[+-]?|VHT(20|40|80|160)|HE(20|40|80|160)|EHT(20|40|80|160|320))$")


def check_value(key: str, value: Any) -> str:
    """A value as uci gets it, or ValueError: nothing that could carry extra lines into hostapd's
    configuration (control characters), and each option within what it can be."""
    if isinstance(value, bool):
        value = "1" if value else "0"
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"{key}: no control characters")
    ok = {
        "ssid": lambda v: 1 <= len(v.encode()) <= 32,
        "mesh_id": lambda v: 1 <= len(v.encode()) <= 32,
        "key": lambda v: 8 <= len(v) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", v),
        "encryption": lambda v: v in ENCRYPTIONS,
        "disabled": lambda v: v in ("0", "1"),
        "hidden": lambda v: v in ("0", "1"),
        "channel": lambda v: v == "auto" or (v.isdigit() and 1 <= int(v) <= 233),
        "htmode": lambda v: bool(HTMODES.match(v)),
        "txpower": lambda v: v.isdigit() and int(v) <= 36,
        "country": lambda v: bool(re.fullmatch(r"[A-Z]{2}|00", v)),
        "mobility_domain": lambda v: bool(re.fullmatch(r"[0-9a-fA-F]{4}", v)),
        **{
            k: (lambda v: v in ("0", "1"))
            for k in ("ieee80211r", "ft_over_ds", "ft_psk_generate_local", "ieee80211k", "bss_transition")
        },
    }.get(key)
    if ok is None or not ok(value):
        raise ValueError(f"{key}: not a value it can have")
    return value


async def wifi_settings(ubus: Ubus) -> dict[str, Any]:
    """The wireless configuration, keys left out (only whether one is set)."""
    values = (await ubus.call("uci", "get", {"config": "wireless"})).get("values", {})
    radios, ifaces = [], []
    for name, sec in values.items():
        if sec.get(".type") == "wifi-device":
            radios.append({"section": name, **{k: sec.get(k) for k in RADIO_FIELDS}, "band": sec.get("band")})
        elif sec.get(".type") == "wifi-iface":
            entry = {"section": name, **{k: sec.get(k) for k in IFACE_FIELDS if k != "key"}}
            entry |= {"key_set": bool(sec.get("key")), "device": sec.get("device"), "mode": sec.get("mode")}
            entry["network"] = sec.get("network")
            ifaces.append(entry)
    return {"radios": radios, "interfaces": ifaces}


def _clean(current: dict[str, Any], changes: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Every change checked before any is staged."""
    out: dict[str, dict[str, str]] = {}
    for section, values in changes.items():
        sec = current.get(section)
        kind = (sec or {}).get(".type")
        if kind not in ("wifi-device", "wifi-iface"):
            raise ValueError(f"no radio or Wi-Fi network {section}")
        allowed = RADIO_FIELDS if kind == "wifi-device" else IFACE_FIELDS
        clean = {
            k: check_value(k, v) for k, v in values.items() if k in allowed and v is not None and v != ""
        }
        if kind == "wifi-iface" and sec is not None:
            encryption = clean.get("encryption", sec.get("encryption") or "none")
            if "key" in clean and encryption in ("none", "owe"):
                raise ValueError(f"{section} is open (no password): choose an encryption too")
            if encryption.startswith("sae") and re.fullmatch(r"[0-9a-fA-F]{64}", clean.get("key", "")):
                raise ValueError("WPA3 takes a passphrase, not a 64-digit hex key")
        if clean:
            out[section] = clean
    return out


async def change_wifi(
    ubus: Ubus,
    changes: dict[str, dict[str, Any]],
    confirm_after: float | None = None,
    *,
    expected_ssid: str | None = None,
    expected_ssids: dict[str, str] | None = None,
) -> None:
    """Set wireless options and apply them with a rollback (see apply_with_rollback)."""
    async with ubus.changing:
        s = await ubus.session_id(min_left=200)
        current = (await ubus.call("uci", "get", {"config": "wireless"}, session=s)).get("values", {})
        if expected_ssid is not None and any(
            current.get(section, {}).get("ssid") != expected_ssid for section in changes
        ):
            raise ValueError("the network changed; refresh before applying")
        if expected_ssids is not None and any(
            current.get(section, {}).get("ssid") != expected_ssids.get(section)
            or current.get(section, {}).get("mode", "ap") != "ap"
            for section in changes
        ):
            raise ValueError("the network changed; refresh before applying")
        clean = _clean(current, changes)
        if not clean:
            raise ValueError("nothing to change")
        try:
            for section, values in clean.items():
                await ubus.call(
                    "uci", "set", {"config": "wireless", "section": section, "values": values}, session=s
                )
        except BaseException:
            with contextlib.suppress(Exception):
                await ubus.call("uci", "revert", {"config": "wireless"}, session=s)
            raise
        await apply_with_rollback(ubus, s, confirm_after)


async def add_network(
    ubus: Ubus, radio: str, ssid: str, key: str, encryption: str, network: str = "lan"
) -> str:
    """A new access point network on a radio, applied with its rollback; its section."""
    check_value("ssid", ssid)
    check_value("encryption", encryption)
    if encryption not in ("none", "owe"):
        check_value("key", key)
    if not re.fullmatch(r"[A-Za-z0-9_]{1,32}", network):
        raise ValueError("not a network name")
    async with ubus.changing:
        s = await ubus.session_id(min_left=200)
        current = (await ubus.call("uci", "get", {"config": "wireless"}, session=s)).get("values", {})
        if (current.get(radio) or {}).get(".type") != "wifi-device":
            raise ValueError(f"no radio {radio}")
        values = {"device": radio, "mode": "ap", "ssid": ssid, "encryption": encryption, "network": network}
        if encryption not in ("none", "owe"):
            values["key"] = key
        try:
            added = await ubus.call(
                "uci", "add", {"config": "wireless", "type": "wifi-iface", "values": values}, session=s
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await ubus.call("uci", "revert", {"config": "wireless"}, session=s)
            raise
        await apply_with_rollback(ubus, s)
        return str(added.get("section", ""))


async def remove_network(ubus: Ubus, section: str, *, expected_ssid: str | None = None) -> None:
    """An access point network removed, applied with its rollback."""
    async with ubus.changing:
        s = await ubus.session_id(min_left=200)
        current = (await ubus.call("uci", "get", {"config": "wireless"}, session=s)).get("values", {})
        sec = current.get(section) or {}
        if sec.get(".type") != "wifi-iface" or sec.get("mode", "ap") != "ap":
            raise ValueError(f"no access point network {section}")
        if expected_ssid is not None and sec.get("ssid") != expected_ssid:
            raise ValueError("the network changed; refresh before removing")
        try:
            await ubus.call("uci", "delete", {"config": "wireless", "section": section}, session=s)
        except BaseException:
            with contextlib.suppress(Exception):
                await ubus.call("uci", "revert", {"config": "wireless"}, session=s)
            raise
        await apply_with_rollback(ubus, s)


async def _radios_up(ubus: Ubus, session: str) -> dict[str, bool]:
    try:
        status = await ubus.call("network.wireless", "status", session=session)
    except UbusError as e:
        if e.code != 2:
            raise
        # OpenWrt 25.12 (see read): the Wi-Fi interfaces iwinfo lists count as the radios up.
        devices = (await ubus.call("iwinfo", "devices", session=session)).get("devices") or []
        return {str(d): True for d in devices}
    return {
        name: bool(r.get("up")) and not r.get("retry_setup_failed")
        for name, r in status.items()
        if not r.get("disabled")
    }


async def apply_with_rollback(ubus: Ubus, session: str, confirm_after: float | None = None) -> None:
    """Apply the session's staged changes. The device undoes them by itself after 90 s unless
    confirmed, and Kastellan confirms only if it still answers and every radio that is meant to
    be on came up; otherwise it rolls back at once."""
    wait = CONFIRM_AFTER if confirm_after is None else confirm_after
    try:
        before = await _radios_up(ubus, session)
        await ubus.call("uci", "apply", {"rollback": True, "timeout": 90}, session=session)
    except BaseException:  # nothing applied: the staged changes mustn't ride along with the next one
        with contextlib.suppress(Exception):
            await ubus.call("uci", "revert", {"config": "wireless"}, session=session)
        raise
    broken: list[str] | None = None  # None: not one status read answered, so nothing is known
    for _ in range(6):  # the radios restart: up to about half a minute
        await asyncio.sleep(wait)
        try:
            now = await _radios_up(ubus, session)
        except Exception:
            continue
        broken = [n for n, up in now.items() if not up and before.get(n, True)]
        if not broken:
            break
    if broken is None or broken:
        with contextlib.suppress(Exception):
            await ubus.call("uci", "rollback", {}, session=session)
        why = ", ".join(broken) + " didn't come up" if broken else "it didn't answer"
        raise ValueError(f"{why} with the change; it was undone")
    await ubus.call("uci", "confirm", {}, session=session)


# --- Firmware ---------------------------------------------------------------------------------

FIRMWARE = "/tmp/firmware.bin"


async def install_firmware(ubus: Ubus, image: bytes, sha256: str, say=None) -> dict[str, Any]:
    """Put an image on the device (checked there by its checksum), let it test the image, and
    start the upgrade (settings kept). The device reboots into it, a few minutes."""
    if hashlib.sha256(image).hexdigest() != sha256:
        raise ValueError("the image doesn't match its checksum")
    async with ubus.changing:
        s = await ubus.session_id(min_left=280)
        info = await ubus.call("system", "info", session=s)
        free = (info.get("memory") or {}).get("available") or (info.get("memory") or {}).get("free")
        if free and free < len(image) + 8 * 1024 * 1024:
            raise ValueError(f"not enough free memory for the image ({free // 2**20} MB free)")
        try:
            got = await ubus.upload(FIRMWARE, image, s)
            if str(got.get("sha256sum", "")).lower() != sha256 or int(got.get("size", -1)) != len(image):
                raise ValueError("the image arrived damaged")
            if say:
                say("on the device, checksum right there too")
            test = await ubus.call("rpc-sys", "upgrade_test", {}, timeout=60, session=s)
            if test.get("code", 0) != 0:
                why = str(test.get("stdout") or test.get("stderr") or "")[:300]
                raise ValueError(f"the device refused the image: {why}")
        except BaseException:
            with contextlib.suppress(Exception):
                await ubus.call("file", "remove", {"path": FIRMWARE}, session=s)
            raise
        await ubus.call("rpc-sys", "upgrade_start", {"keep": True}, timeout=30, session=s)
    return {"started": True}
