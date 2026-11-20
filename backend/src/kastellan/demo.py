"""Demo mode (KASTELLAN_DEMO=1): a made-up home network whose check results are simulated.

It is for trying the app and for the website's pictures. Nothing is contacted. Results are a pure
function of (device, check, time), so the backfilled history and the live results agree, and a
short story plays out over the last day: the printer was off for an hour, a camera on Wi-Fi
dropped out a few times, the gaming PC was on in the evening, and Jellyfin went down a few
minutes ago.
"""

from __future__ import annotations

import math
import random
import zlib
from datetime import UTC, datetime, timedelta

from .checks import Result
from .config import Check, Config, Device
from .history import History, Row
from .software import Software
from .versions import Versions

CONFIG = {
    "check_interval": 15,
    "devices": {
        "fritzbox": {
            "name": "FRITZ!Box 7590",
            "host": "192.168.178.1",
            "kind": "router",
            "driver": "fritzbox",
            "tags": ["mesh master"],
            "checks": ["ping", {"http": "https://192.168.178.1", "name": "web"}],
        },
        "switch": {
            "name": "Office switch",
            "host": "192.168.178.2",
            "kind": "switch",
            "parent": "fritzbox",
        },
        "repeater-living": {
            "name": "Repeater living room",
            "host": "192.168.178.3",
            "kind": "access-point",
            "parent": "fritzbox",
            "tags": ["mesh"],
        },
        "repeater-attic": {
            "name": "Repeater attic",
            "host": "192.168.178.4",
            "kind": "access-point",
            "parent": "repeater-living",
            "tags": ["mesh"],
        },
        "nas": {
            "name": "NAS",
            "host": "nas.lan",
            "kind": "server",
            "parent": "switch",
            "tags": ["docker"],
            "docker": {"wud": "http://nas.lan:3000"},
            "checks": [
                "ping",
                {"tcp": 22, "name": "ssh"},
                {"http": "http://nas.lan:8096/health", "name": "jellyfin"},
                {"http": "https://nas.lan:5001", "name": "dsm"},
            ],
        },
        "homeserver": {
            "name": "Home server",
            "host": "homeserver.lan",
            "kind": "server",
            "parent": "switch",
            "tags": ["docker", "proxmox"],
            "docker": {"wud": "http://homeserver.lan:3000"},
            "checks": [
                "ping",
                {"tcp": 22, "name": "ssh"},
                {"http": "https://homeserver.lan:8006", "name": "proxmox"},
                {"http": "http://homeserver.lan:8123", "name": "home assistant"},
            ],
        },
        "pi": {
            "name": "Raspberry Pi",
            "host": "pi.lan",
            "kind": "server",
            "parent": "switch",
            "checks": ["ping", {"tcp": 22, "name": "ssh"}, {"tcp": 53, "name": "dns"}],
        },
        "gaming-pc": {
            "name": "Gaming PC",
            "host": "gaming-pc.lan",
            "kind": "pc",
            "parent": "switch",
            "checks": ["ping", {"tcp": 3389, "name": "rdp"}],
        },
        "office-laptop": {
            "name": "Office laptop",
            "host": "office-laptop.lan",
            "kind": "laptop",
        },
        "macbook": {
            "name": "MacBook",
            "host": "macbook.lan",
            "kind": "laptop",
            "checks": ["ping", {"tcp": 22, "name": "ssh"}],
        },
        "printer": {
            "name": "Printer",
            "host": "printer.lan",
            "kind": "printer",
            "parent": "switch",
            "checks": ["ping", {"tcp": 631, "name": "ipp"}],
        },
        "cam-door": {
            "name": "Front door camera",
            "host": "cam-door.lan",
            "kind": "camera",
            "parent": "fritzbox",
            "checks": ["ping", {"tcp": 554, "name": "rtsp"}],
        },
        "cam-garden": {
            "name": "Garden camera",
            "host": "cam-garden.lan",
            "kind": "camera",
            "checks": ["ping", {"tcp": 554, "name": "rtsp"}],
        },
    },
}

# Typical round trip per device, in ms: wired is fast, each mesh hop adds a few.
BASE_LATENCY = {
    "fritzbox": 0.6,
    "switch": 0.9,
    "repeater-living": 3.5,
    "repeater-attic": 7.5,
    "nas": 1.1,
    "homeserver": 0.9,
    "pi": 1.4,
    "gaming-pc": 0.8,
    "office-laptop": 6.0,
    "macbook": 5.0,
    "printer": 2.2,
    "cam-door": 1.8,
    "cam-garden": 11.0,
}


# What the services say they are. The home server's SSH got an update six hours ago.
SOFTWARE = {
    ("fritzbox", "web"): Software("FRITZ!Box"),
    ("nas", "ssh"): Software("OpenSSH", "9.6p1", "Ubuntu"),
    ("nas", "jellyfin"): Software("Jellyfin", "10.10.7"),
    ("nas", "dsm"): Software("nginx"),
    ("homeserver", "ssh"): Software("OpenSSH", "10.2p1", "Debian"),
    ("homeserver", "proxmox"): Software("pve-api-daemon", "3.0"),
    ("homeserver", "home assistant"): Software("Home Assistant"),
    ("pi", "ssh"): Software("OpenSSH", "9.2p1", "Raspbian"),
    ("macbook", "ssh"): Software("OpenSSH", "9.9"),
    ("printer", "ipp"): None,
}
UPDATED = ("homeserver", "ssh", Software("OpenSSH", "10.0p2", "Debian"), 360)  # before, minutes ago


# What the demo's devices with a driver report (see hub._poll_drivers).
INFO = {
    "fritzbox": {"model": "FRITZ!Box 7590", "name": "fritz.box", "firmware": "FRITZ!OS 8.20", "details": {}},
}


def _c(name, image, tag, update_to=None, unchecked=None, state="running"):
    return {
        "key": name,
        "name": name,
        "image": image,
        "tag": tag,
        "image_id": "3f9a2c71b0de",
        "state": state,
        "update": update_to is not None,
        "update_to": update_to,
        "unchecked": unchecked,
        "registry": "hub",
    }


# The demo's Docker hosts (see hub._poll_docker).
CONTAINERS = {
    "nas": [
        _c("jellyfin", "jellyfin/jellyfin", "10.10.7", update_to="10.11.0"),
        _c("immich-server", "immich-app/immich-server", "v1.135.3"),
        _c("paperless", "paperless-ngx/paperless-ngx", "2.14"),
        _c("sonarr", "linuxserver/sonarr", "latest", unchecked="Unsupported Registry unknown"),
    ],
    "homeserver": [
        _c("homeassistant", "home-assistant/home-assistant", "2026.11.2", update_to="2026.11.3"),
        _c(
            "mosquitto",
            "library/eclipse-mosquitto",
            "2",
            unchecked="only newer tags are checked, not new builds of :2",
        ),
        _c("zigbee2mqtt", "koenkk/zigbee2mqtt", "latest", update_to="newer build"),
        _c(
            "wud",
            "getwud/wud",
            "latest",
            unchecked="WUD doesn't watch the digest of :latest (label wud.watch.digest=true)",
        ),
    ],
}


def config() -> Config:
    return Config.model_validate(CONFIG)


class Demo:
    def __init__(self, now: datetime | None = None) -> None:
        self.t0 = now or datetime.now(UTC)

    def _ago(self, t: datetime) -> float:
        """Minutes before the demo started; the present stays like the start."""
        return max(0.0, (self.t0 - t).total_seconds() / 60)

    def result(self, id: str, check: Check, t: datetime) -> Result:
        ago = self._ago(t)
        rng = random.Random(zlib.crc32(f"{id}/{check.name}/{int(t.timestamp()) // 15}".encode()))
        down = self._down(id, check.name, ago, rng)
        if down:
            return Result(False, detail=down)
        base = BASE_LATENCY.get(id, 2.0)
        # A slow daily wave (busier evenings) and some jitter; HTTP adds the server's time.
        wave = 1 + 0.25 * math.sin((t.hour * 60 + t.minute) / 1440 * 2 * math.tau)
        latency = base * wave * rng.uniform(0.8, 1.35)
        if rng.random() < 0.02:
            latency *= rng.uniform(2, 5)
        software = self.software(id, check.name, ago)
        if check.type == "http":
            latency += rng.uniform(8, 30)
            return Result(True, round(latency, 2), "HTTP 200", software)
        if check.type == "tcp":
            latency += rng.uniform(0.1, 0.6)
        return Result(True, round(latency, 2), software=software)

    @staticmethod
    def software(id: str, check: str, ago: float) -> Software | None:
        device, name, before, when = UPDATED
        if (id, check) == (device, name) and ago > when:
            return before
        return SOFTWARE.get((id, check))

    def _down(self, id: str, check: str, ago: float, rng: random.Random) -> str:
        """Why the check fails at `ago` minutes before now, or ''."""
        match id:
            case "printer" if 240 <= ago <= 305:
                return "no reply"
            case "gaming-pc" if not 150 <= ago <= 520:
                return "no reply" if check == "ping" else "timed out"
            case "office-laptop" if 840 <= ago <= 1290:
                return "no reply"
            case "cam-garden" if 560 <= ago <= 640 and rng.random() < 0.35:
                return "no reply" if check == "ping" else "timed out"
            case "nas" if check == "jellyfin" and ago <= 7:
                return "HTTP 502"
            case "homeserver" if check == "home assistant" and 95 <= ago <= 98:
                return "ConnectError: connection refused"
        return ""

    async def run(self, id: str, device: Device, check: Check) -> Result:
        return self.result(id, check, datetime.now(UTC))

    def backfill(
        self,
        history: History,
        config: Config,
        versions: Versions | None = None,
        hours: float = 24,
        step: float = 60,
    ) -> None:
        """Write simulated results for the past `hours`, one every `step` seconds, and the
        software the services showed (with the one update) to the version log."""
        start = self.t0 - timedelta(hours=hours)
        if versions is not None:
            device, name, before, when = UPDATED
            for (id, check), software in SOFTWARE.items():
                first = before if (id, check) == (device, name) else software
                if first is not None:
                    versions.record(start, id, check, None, str(first))
            versions.record(
                self.t0 - timedelta(minutes=when), device, name, str(before), str(SOFTWARE[(device, name)])
            )
        n = int(hours * 3600 / step)
        rows = []
        for i in range(n):
            t = start + timedelta(seconds=i * step)
            for id, device in config.devices.items():
                for check in device.checks:
                    r = self.result(id, check, t)
                    rows.append(Row(t, id, check.name, r.up, r.latency_ms, r.detail))
        history.extend(rows)
