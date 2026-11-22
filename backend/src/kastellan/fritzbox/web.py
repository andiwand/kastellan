"""What a FRITZ!Box shows only in its web interface: the Wi-Fi environment (neighbouring
networks, channel use), its WireGuard connections and its energy monitor (`ecoStat`: CPU, memory,
temperature; vitals.py reads it). Through the web interface's own API
(`/login_sid.lua`, `/data.lua`), which AVM does not document and may change with any FRITZ!OS
update: every reading here is defensive, and a change shows as "can't be read", never as wrong
data. Seen on FRITZ!OS 8.25 (FRITZ!Box 7530).

The same FRITZ!Box user as TR-064 signs in (the PBKDF2 challenge of FRITZ!OS 7.24+, MD5 before),
on port 443 over the same pinned certificate. The session is kept and renewed when it ends.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode

from .client import Fritz, FritzError

WEB_PORT = 443
NO_SESSION = "0000000000000000"
# A neighbour's strength in the environment list is 0..5 bars: roughly these dBm.
BARS = {0: -90, 1: -80, 2: -72, 3: -65, 4: -58, 5: -50}


def response(challenge: str, password: str) -> str:
    """The answer to a login challenge: PBKDF2 ("2$iter1$salt1$iter2$salt2") or MD5."""
    if challenge.startswith("2$"):
        _, iter1, salt1, iter2, salt2 = challenge.split("$")
        if (
            not (1 <= int(iter1) <= 1_000_000 and 1 <= int(iter2) <= 1_000_000)
            or max(len(salt1), len(salt2)) > 128
        ):
            raise ValueError("invalid FRITZ!Box login challenge")
        hash1 = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt1), int(iter1))
        hash2 = hashlib.pbkdf2_hmac("sha256", hash1, bytes.fromhex(salt2), int(iter2))
        return f"{salt2}${hash2.hex()}"
    text = f"{challenge}-{password}".encode("utf-16-le")
    return f"{challenge}-{hashlib.md5(text).hexdigest()}"


class Web:
    """The web interface's API of one FRITZ!Box (with its TR-064 client's user and pin)."""

    def __init__(self, fb: Fritz) -> None:
        self.fb = fb
        self.sid: str | None = None
        self.since = 0.0
        self._lock = asyncio.Lock()

    async def _post(
        self, path: str, form: dict[str, str], timeout: float = 30, idempotent: bool = False
    ) -> tuple[int, bytes]:
        body = urlencode(form).encode()
        status, _, payload = await self.fb.http(
            "POST",
            path,
            {"Content-Type": "application/x-www-form-urlencoded"},
            body,
            timeout,
            port=WEB_PORT,
            idempotent=idempotent,
        )
        return status, payload

    async def login(self) -> str:
        async with self._lock:
            if self.sid and time.monotonic() - self.since < 600:
                return self.sid
            _, _, payload = await self.fb.http("GET", "/login_sid.lua?version=2", {}, b"", 20, port=WEB_PORT)
            root = ET.fromstring(payload)
            challenge = root.findtext("Challenge") or ""
            block = int(root.findtext("BlockTime") or 0)
            if block:
                raise FritzError(0, f"the FRITZ!Box blocks sign-ins for {block} s (too many wrong ones)")
            answer = await asyncio.to_thread(response, challenge, self.fb.password)
            _status, payload = await self._post(
                "/login_sid.lua?version=2", {"username": self.fb.user, "response": answer}
            )
            sid = ET.fromstring(payload).findtext("SID") or NO_SESSION
            if sid == NO_SESSION:
                raise FritzError(0, "the FRITZ!Box web interface refused Kastellan's user")
            self.sid, self.since = sid, time.monotonic()
            return sid

    async def data(self, page: str, xhr_id: str = "all") -> dict[str, Any]:
        """One page's data (`data.lua`); signs in again once if the session ended."""
        if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", page + xhr_id):
            raise ValueError("not a page")
        for again in (False, True):
            sid = await self.login()
            status, payload = await self._post(
                "/data.lua",
                {"xhr": "1", "sid": sid, "lang": "en", "page": page, "xhrId": xhr_id},
                idempotent=True,  # it only reads
            )
            try:
                answer = json.loads(payload)
            except ValueError:
                answer = None
            if status == 200 and isinstance(answer, dict) and isinstance(answer.get("data"), dict):
                self.since = time.monotonic()
                return answer["data"]
            if again:
                break
            self.sid = None  # the session ended: once more
        raise FritzError(0, f"the FRITZ!Box web interface gave no data for {page} (FRITZ!OS changed?)")


# --- The Wi-Fi environment --------------------------------------------------------------------


def environment(data: dict[str, Any]) -> dict[str, Any]:
    """`chan`/`environment`: per band the channel in use, the networks heard (name, channel,
    signal in approximate dBm) and how busy the air is; the box's own entries left out."""
    bands: dict[str, Any] = {}
    scan = data.get("scanlist") if isinstance(data.get("scanlist"), list) else []
    for key, band in (("24ghz", "2g"), ("5ghz", "5g"), ("6ghz", "6g")):
        b = data.get(key)
        if not isinstance(b, dict):
            continue
        own = {int(c) for c in b.get("usedChannels") or [] if str(c).isdigit()}
        neighbours = []
        for n in scan:
            if not isinstance(n, dict) or n.get("bandId") != key:
                continue
            # The box's own networks come first, as text, at full strength.
            if isinstance(n.get("channel"), str) and n.get("nrssi") == 5:
                continue
            try:
                channel = int(n.get("channel"))
            except (TypeError, ValueError):
                continue
            neighbours.append(
                {
                    "ssid": str(n.get("ssid") or "")[:64],
                    "bssid": str(n.get("mac") or "").lower()[:17],
                    "channel": channel,
                    "band": band,
                    "signal": BARS.get(n.get("nrssi"), -90),
                    "width": None,
                    "encrypted": None,
                }
            )
        busy = None
        air = str(b.get("airtimedata") or "").split(",")
        if len(air) > 3:  # "time,?,seconds, busy:own, ..." per minute: the last ones
            values = [int(x.split(":")[0]) for x in air[3:] if re.fullmatch(r"\d+:\d+", x)]
            busy = round(sum(values[-10:]) / len(values[-10:])) if values else None
        channels = [
            {
                "channel": c.get("value"),
                "dfs": bool(c.get("dfs")),
                "interference": bool(c.get("interference")),
            }
            for c in b.get("channels") or []
            if isinstance(c, dict) and isinstance(c.get("value"), int)
        ]
        bands[band] = {
            "channel": next(iter(own), None) or b.get("currentChannel") or None,
            "neighbours": neighbours,
            "busy": busy,
            "channels": channels,
            "auto": data.get("channelSelectMode") == "auto",
        }
    return bands


# --- WireGuard --------------------------------------------------------------------------------


def wireguard(data: dict[str, Any]) -> dict[str, Any]:
    """`shareWireguard`: the box's WireGuard server and its connections (to devices and to other
    networks). Fields of a connection are taken by the names they are likely to have and kept
    as they are otherwise: a changed FRITZ!OS shows less, never wrong."""
    init = data.get("init") if isinstance(data.get("init"), dict) else {}
    if not all(isinstance(init.get(k), list) for k in ("userConnections", "boxConnections")):
        raise FritzError(0, "the FRITZ!Box WireGuard connection list can't be read")

    def connection(c: Any, kind: str) -> dict[str, Any] | None:
        if not isinstance(c, dict):
            return None

        def pick(*names: str) -> Any:
            for n in names:
                if c.get(n) not in (None, ""):
                    return c[n]
            return None

        return {
            "kind": kind,
            "name": str(pick("name", "displayName", "description") or "?")[:80],
            "active": pick("active", "enabled", "isActive", "connected"),
            "status": pick("status", "state", "connectionState"),
            "last_handshake": pick("lastHandshake", "last_handshake", "latestHandshake", "handshake"),
            "endpoint": pick("endpoint", "remoteEndpoint", "domain"),
            "networks": pick("remoteNetworks", "allowedIps", "remoteNet", "net"),
        }

    connections = [
        x
        for kind, key in (("device", "userConnections"), ("network", "boxConnections"))
        for x in (connection(c, kind) for c in init.get(key) or [])
        if x
    ]
    error = init.get("wg_global_error")
    return {
        "server": init.get("server") or None,
        "port": init.get("wg_list_port") or None,
        "address": init.get("own_ipv4") or None,
        "connections": connections,
        "error": (error.get("eventMsg") or None) if isinstance(error, dict) else None,
    }
