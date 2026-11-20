"""Talking to an OpenWrt device: ubus over HTTPS (/ubus, JSON-RPC), as LuCI does, with
Kastellan's own login (setup.py), and what is read and done with it.

The device's certificate is pinned by its fingerprint: the setup reads it on the device (over
the SSH connection whose host key is checked) and the hub compares every connection's
certificate with it before a byte is sent. Validity dates don't count (uhttpd's own certificate
is never renewed, and a router's clock may be wrong); only the certificate itself. A device
that shows another one gets nothing until its login is set up again.

Requests are plain HTTP/1.0 over that checked TLS connection, one per call.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import ssl
import time
from pathlib import Path
from typing import Any

import httpx

NULL_SESSION = "0" * 32
DENIED = -32002  # uhttpd: the session may not call that (or it has ended)
MAX_RESPONSE = 8 * 1024 * 1024


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


def _decode_chunked(body: bytes) -> bytes:
    out, rest = bytearray(), body
    while rest:
        size_line, _, rest = rest.partition(b"\r\n")
        size = int(size_line.split(b";")[0] or b"0", 16)
        if size == 0:
            break
        out += rest[:size]
        rest = rest[size + 2 :]
    return bytes(out)


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
        self.changing = asyncio.Lock()  # one change (Wi-Fi, firmware, services) at a time

    async def _http(self, path: str, body: bytes, content_type: str, timeout: float) -> tuple[int, bytes]:
        if self.transport is not None:
            async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as c:
                r = await c.post(
                    f"https://{self.host}:{self.port}{path}",
                    content=body,
                    headers={"content-type": content_type},
                )
                return r.status_code, r.content
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # checked below, by the pinned fingerprint
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(self.host, self.port, ssl=ctx)
            try:
                der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
                pin = self.pins.get(self.id)
                if pin is None:
                    raise ChangedCertificate(f"{self.host}: no pinned certificate (set its login up)")
                if fingerprint_of_der(der) != pin:
                    raise ChangedCertificate(
                        f"{self.host} shows another certificate than at its setup"
                        " (set its login up again if it was reset)"
                    )
                head = (
                    f"POST {path} HTTP/1.0\r\nHost: {self.host}\r\nContent-Type: {content_type}\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
                )
                writer.write(head.encode() + body)
                await writer.drain()
                data = bytearray()
                while len(data) <= MAX_RESPONSE:
                    try:
                        chunk = await reader.read(65536)
                    except (ssl.SSLError, ConnectionResetError, asyncio.IncompleteReadError):
                        break  # uhttpd closes without a TLS goodbye
                    if not chunk:
                        break
                    data += chunk
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(writer.wait_closed(), 2)
        header, _, payload = bytes(data).partition(b"\r\n\r\n")
        lines = header.decode("latin-1").split("\r\n")
        try:
            status = int(lines[0].split()[1])
        except (IndexError, ValueError) as e:
            raise ConnectionError(f"{self.host}: not an HTTP answer") from e
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        if "chunked" in headers.get("transfer-encoding", "").lower():
            payload = _decode_chunked(payload)
        return status, payload

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
    board = await ubus.call("system", "board")
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
    with contextlib.suppress(Exception):
        for lease in (await ubus.call("luci-rpc", "getDHCPLeases")).get("dhcp_leases", []):
            if lease.get("macaddr") and lease.get("hostname"):
                leases[lease["macaddr"].lower()] = lease["hostname"]
    try:
        radios = []
        for name, radio in (await ubus.call("network.wireless", "status")).items():
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
                            mac = (c.get("mac") or "").lower()
                            clients.append(
                                {
                                    "mac": mac,
                                    "name": leases.get(mac),
                                    "signal": c.get("signal"),
                                    "inactive": c.get("inactive"),
                                }
                            )
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
                    "band": cfg.get("band") or cfg.get("hwmode"),
                    "channel": cfg.get("channel"),
                    "htmode": cfg.get("htmode"),
                    "interfaces": ifaces,
                }
            )
        report["radios"] = radios
    except Exception as e:
        report["errors"]["wifi"] = str(e)[:200]
    try:
        rc = await ubus.call("rc", "list")
        report["services"] = [
            {"name": n, "enabled": bool(v.get("enabled")), "running": bool(v.get("running"))}
            for n, v in sorted(rc.items())
        ][:200]
    except Exception as e:  # rc arrived in OpenWrt 21.02
        report["errors"]["services"] = str(e)[:200]
    from .setup import SERVICES_ALLOW

    try:  # the services Kastellan may act on
        data = (await ubus.call("file", "read", {"path": SERVICES_ALLOW})).get("data", "")
        report["services_allowed"] = [ln for ln in str(data).split("\n") if ln][:100]
    except Exception:
        report["services_allowed"] = []
    return report


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


async def reboot(ubus: Ubus) -> None:
    await ubus.call("system", "reboot")


# --- Wi-Fi ------------------------------------------------------------------------------------

# What the page may change, per kind of section; nothing else in `wireless` is touched.
RADIO_FIELDS = ("channel", "htmode", "txpower", "disabled", "country")
IFACE_FIELDS = ("ssid", "key", "encryption", "disabled", "hidden", "mesh_id")
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
        if clean:
            out[section] = clean
    return out


async def change_wifi(ubus: Ubus, changes: dict[str, dict[str, Any]], confirm_after: float = 5) -> None:
    """Set wireless options and apply them with a rollback (see apply_with_rollback)."""
    async with ubus.changing:
        s = await ubus.session_id(min_left=200)
        current = (await ubus.call("uci", "get", {"config": "wireless"}, session=s)).get("values", {})
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


async def add_wifi_iface(ubus: Ubus, values: dict[str, Any], confirm_after: float = 5) -> str:
    """A new wifi-iface section (a mesh, say), checked, applied with a rollback. Its name."""
    allowed = {"device", "mode", "network", "mesh_fwding", *IFACE_FIELDS}
    clean: dict[str, str] = {}
    for k, v in values.items():
        if k not in allowed:
            raise ValueError(f"{k}: not something Kastellan sets")
        if k in IFACE_FIELDS:
            clean[k] = check_value(k, v)
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", str(v)):
            raise ValueError(f"{k}: not a value it can have")
        else:
            clean[k] = str(v)
    async with ubus.changing:
        s = await ubus.session_id(min_left=200)
        try:
            added = await ubus.call(
                "uci", "add", {"config": "wireless", "type": "wifi-iface", "values": clean}, session=s
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await ubus.call("uci", "revert", {"config": "wireless"}, session=s)
            raise
        await apply_with_rollback(ubus, s, confirm_after)
        return str(added.get("section", ""))


async def _radios_up(ubus: Ubus, session: str) -> dict[str, bool]:
    status = await ubus.call("network.wireless", "status", session=session)
    return {
        name: bool(r.get("up")) and not r.get("retry_setup_failed")
        for name, r in status.items()
        if not r.get("disabled")
    }


async def apply_with_rollback(ubus: Ubus, session: str, confirm_after: float = 5) -> None:
    """Apply the session's staged changes. The device undoes them by itself after 90 s unless
    confirmed, and Kastellan confirms only if it still answers and every radio that is meant to
    be on came up; otherwise it rolls back at once."""
    before = await _radios_up(ubus, session)
    await ubus.call("uci", "apply", {"rollback": True, "timeout": 90}, session=session)
    broken: list[str] = []
    for _ in range(6):  # the radios restart: up to about half a minute
        await asyncio.sleep(confirm_after)
        try:
            now = await _radios_up(ubus, session)
        except Exception:
            continue
        broken = [n for n, up in now.items() if not up and before.get(n, True)]
        if not broken:
            break
    if broken:
        with contextlib.suppress(Exception):
            await ubus.call("uci", "rollback", {}, session=session)
        raise ValueError(f"{', '.join(broken)} didn't come up with the change; it was undone")
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
