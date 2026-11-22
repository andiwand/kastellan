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
from urllib.parse import urlsplit

from .checks import Result
from .config import Check, Config, Device
from .demo_more import FRITZ
from .history import History, Row
from .software import Software
from .tls import Cert, public_name
from .versions import Versions
from .vitals import Vitals

CONFIG = {
    "check_interval": 15,
    "sites": {"hetzner": "Hetzner Cloud"},
    "groups": {"aps": {"name": "Access points", "devices": ["repeater-living", "repeater-attic"]}},
    "backups": {
        "nas-b2": {"name": "Photos to B2 (restic)", "device": "nas"},
        "homeserver-borg": {"name": "Home server (borg)", "device": "homeserver", "within_hours": 30},
    },
    "maintenance": [
        {"name": "Patch night", "groups": ["aps"], "days": ["sun"], "at": "03:00", "minutes": 60}
    ],
    "devices": {
        "fritzbox": {
            "name": "FRITZ!Box 7590",
            "host": "192.168.178.1",
            "kind": "router",
            "driver": "fritzbox",
            "fritzbox": {"user": "kastellan"},
            "tags": ["mesh master"],
            "checks": ["ping", {"http": "https://192.168.178.1", "name": "web"}],
        },
        "switch": {
            "name": "Office switch",
            "host": "192.168.178.2",
            "kind": "switch",
            "powered_by": "ups",
            "parent": "fritzbox",
        },
        "repeater-living": {
            "name": "Repeater living room",
            "host": "192.168.178.3",
            "kind": "access-point",
            "parent": "fritzbox",
            "tags": ["mesh"],
            "openwrt": {},
            # Its own services show under these: dropbear under ssh, uhttpd under luci.
            "checks": ["ping", {"tcp": 22, "name": "ssh"}, {"http": "https://192.168.178.3", "name": "luci"}],
        },
        "repeater-attic": {
            "name": "Repeater attic",
            "host": "192.168.178.4",
            "kind": "access-point",
            "parent": "repeater-living",
            "tags": ["mesh"],
            "openwrt": {},
        },
        "nas": {
            "name": "NAS",
            "host": "nas.lan",
            "kind": "server",
            "powered_by": "ups",
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
            "powered_by": "ups",
            "parent": "switch",
            "tags": ["docker", "proxmox"],
            "docker": {"wud": "http://homeserver.lan:3000"},
            "collect": {},
            "checks": [
                "ping",
                {"tcp": 22, "name": "ssh"},
                {"http": "https://homeserver.lan:8006", "name": "proxmox"},
                {"http": "http://homeserver.lan:8123", "name": "home assistant"},
                # The same service by its public name: out to the internet address and back in.
                {"http": "https://home.example.net", "name": "home assistant (public)"},
            ],
        },
        # Outside: a small server on the internet, with the family website.
        "vps": {
            "name": "Website server",
            "host": "203.0.113.20",
            "kind": "server",
            "site": "hetzner",
            "exposed": True,
            "checks": [
                "ping",
                {"tcp": 22, "name": "ssh"},
                {"http": "https://www.example.net", "name": "website"},
                # Does the website's name still point here?
                {"dns": "www.example.net", "expect": "203.0.113.20", "name": "dns"},
            ],
        },
        "pi": {
            "name": "Raspberry Pi",
            "host": "pi.lan",
            "kind": "server",
            "parent": "switch",
            "checks": ["ping", {"tcp": 22, "name": "ssh"}, {"tcp": 53, "name": "dns"}],
        },
        "windows": {  # a virtual machine on the home server (its windows-11 VM)
            "name": "Windows VM",
            "host": "windows.lan",
            "kind": "pc",
            "parent": "switch",
            "runs_on": {"host": "homeserver", "vm": "windows-11"},
            "checks": ["ping", {"tcp": 3389, "name": "rdp"}],
        },
        "ups": {  # on the home server's USB, read by its collector (apcupsd)
            "name": "UPS",
            "kind": "ups",
            "ups": {"via": "homeserver"},
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


def _c(
    name,
    image,
    tag,
    update_to=None,
    unchecked=None,
    state="running",
    previous=None,
    within=None,
    compose=None,
    ports=(),
):
    return {
        "key": name,
        "wud_id": name,
        "name": name,
        "image": image,
        "tag": tag,
        "image_id": "3f9a2c71b0de",
        "state": state,
        "update": update_to is not None,
        "update_to": update_to,
        "update_digest": "",
        "unchecked": unchecked,
        "registry": "hub",
        "previous": previous,
        "update_within": within,
        "compose": compose,
        "ports": list(ports),
    }


# The demo's Docker hosts (see hub._poll_docker).
CONTAINERS = {
    "nas": [
        _c(
            "jellyfin",
            "jellyfin/jellyfin",
            "10.10.7",
            update_to="10.11.0",
            compose="/srv/media/compose.yaml",
            ports=[8096],
        ),
        _c("postgres", "library/postgres", "16.4", update_to="17.2", within="16.6"),
        _c("immich-server", "immich-app/immich-server", "v1.135.3"),
        _c("paperless", "paperless-ngx/paperless-ngx", "2.14", previous="8d41e07c5a92"),
        _c("sonarr", "linuxserver/sonarr", "latest", unchecked="Unsupported Registry unknown"),
        _c("calibre-web", "linuxserver/calibre-web", "0.6.24", state="exited"),
    ],
    "homeserver": [
        _c(
            "homeassistant", "home-assistant/home-assistant", "2026.11.2", update_to="2026.11.3", ports=[8123]
        ),
        _c(
            "mosquitto",
            "library/eclipse-mosquitto",
            "2",
            unchecked="only newer tags are checked, not new builds of :2",
        ),
        _c("zigbee2mqtt", "koenkk/zigbee2mqtt", "latest", update_to="newer build"),
        _c("wg-easy", "wg-easy/wg-easy", "15.1.0", ports=[51820, 51821]),
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


# What the home server's collector reports (host/collector.py), made up.
# Days until full at the rate each fills (disks.py), as the hub would have learned them.
FORECASTS = {"homeserver": {"/srv/media": 23.5}}

HOST = {
    "homeserver": {
        "version": 1,
        "errors": {},
        "system": {
            "hostname": "homeserver",
            "kernel": "6.8.0-49-generic",
            "uptime": 1_830_000.0,
            "load": [0.42, 0.51, 0.48],
            "cpus": 8,
            "memory": {"total": 32 * 2**30, "available": 21 * 2**30},
            "swap": {"total": 8 * 2**30, "free": 8 * 2**30},
        },
        "filesystems": [
            {
                "mount": "/",
                "device": "/dev/nvme0n1p2",
                "type": "ext4",
                "total": 500e9,
                "used": 212e9,
                "available": 263e9,
            },
            {
                "mount": "/srv/media",
                "device": "/dev/md0",
                "type": "ext4",
                "total": 8e12,
                "used": 7.0e12,
                "available": 0.6e12,
            },
        ],
        "raid": [
            {
                "name": "md0",
                "active": True,
                "level": "raid1",
                "devices": ["sda", "sdb"],
                "failed": [],
                "status": "UU",
                "progress": None,
            }
        ],
        "smart": {
            "available": True,
            "disks": [
                {
                    "disk": "nvme0n1",
                    "model": "Samsung SSD 980 1TB",
                    "passed": True,
                    "temperature": 41,
                    "power_on_hours": 18_200,
                    "nvme_used_percent": 4,
                    "nvme_media_errors": 0,
                },
                {
                    "disk": "sda",
                    "model": "WDC WD80EFZZ",
                    "passed": True,
                    "temperature": 36,
                    "power_on_hours": 26_000,
                    "reallocated": 0,
                    "pending": 0,
                    "uncorrectable": 0,
                },
                {
                    "disk": "sdb",
                    "model": "WDC WD80EFZZ",
                    "passed": True,
                    "temperature": 37,
                    "power_on_hours": 26_000,
                    "reallocated": 0,
                    "pending": 0,
                    "uncorrectable": 0,
                },
            ],
        },
        "temperatures": [
            {"sensor": "k10temp Tctl", "celsius": 48.0, "limit": None},
            {"sensor": "nvme Composite", "celsius": 41.0, "limit": 82.0},
        ],
        "services": {"failed": []},
        "updates": {
            "reboot": True,
            "reboot_for": ["linux-image-6.8.0-51-generic"],
            "available": 3,
            "security": 1,
        },
        "docker": None,
        "ups": [
            {
                "source": "apcupsd",
                "name": "homeserver-ups",
                "model": "Back-UPS XS 700U",
                "serial": "3B2219X41723",
                "firmware": "924.Z3 .I USB FW:Z3",
                "status": "ONLINE",
                "flags": ["online"],
                "charge": 100.0,
                "runtime": 2940.0,
                "load": 21.0,
                "input_voltage": 231.0,
                "battery_voltage": 13.6,
                "nominal_power": 390.0,
                "last_transfer": "Unacceptable line voltage changes",
                "on_battery_since": "2026-11-12 03:41:07 +0100",
                "off_battery_since": "2026-11-12 03:41:52 +0100",
                "transfers": 3,
                "battery_date": "2024-03-18",
            }
        ],
        "vms": [
            {
                "name": "windows-11",
                "uuid": "5d1c2a8e-7b3f-4e0a-9c41-2f8e6d0b7a13",
                "state": "running",
                "autostart": True,
                "persistent": True,
                "vcpus": 4,
                "memory": 8 * 1024**3,
                "memory_used": 8 * 1024**3,
                "disks": [{"target": "vda", "source": "/var/lib/libvirt/images/windows-11.qcow2"}],
            },
            {
                "name": "debian-test",
                "uuid": "a0b6e2f1-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
                "state": "shut off",
                "autostart": False,
                "persistent": True,
                "vcpus": 2,
                "memory": 2 * 1024**3,
                "memory_used": 2 * 1024**3,
                "disks": [{"target": "vda", "source": "/var/lib/libvirt/images/debian-test.qcow2"}],
            },
        ],
    }
}

HUB_ADDRESS = "192.168.178.5"
GATEWAY = "192.168.178.1"  # the FRITZ!Box


def addresses(hosts: set[str]) -> dict[str, str | None]:
    """Made-up addresses: names at home are in 192.168.178.0/24, the rest as written."""
    out: dict[str, str | None] = {}
    for host in sorted(hosts):
        if host.endswith(".lan"):
            out[host] = f"192.168.178.{20 + zlib.crc32(host.encode()) % 200}"
        else:
            out[host] = host
    return out


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
            return Result(True, round(latency, 2), "HTTP 200", software, *self.cert(str(check.target)))
        if check.type == "dns":
            return Result(True, round(latency + rng.uniform(5, 20), 2), ", ".join(check.expect or []))
        if check.type == "tcp":
            latency += rng.uniform(0.1, 0.6)
        return Result(True, round(latency, 2), software=software)

    def cert(self, url: str) -> tuple[Cert | None, str | None]:
        """A public name: a Let's Encrypt certificate ending soon (an alarm to show), reached
        through the internet address. At home: self-signed, as devices are."""
        host = urlsplit(url).hostname or ""
        if not url.startswith("https://"):
            return None, None
        if public_name(host):
            ends = self.t0 + timedelta(days=9, hours=5)
            return Cert(True, None, ends, "Let's Encrypt", (host,)), "203.0.113.7"
        return Cert(False, "self-signed certificate", self.t0 + timedelta(days=3650), host, (host,)), None

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


# The demo's OpenWrt access points (see hub._poll_routers).
def _router(hostname: str, model: str, version: str, clients: list[tuple[str, int]], up: int) -> dict:
    return {
        "board": {
            "model": model,
            "hostname": hostname,
            "board_name": "cudy,wr3000-v1",
            "release": {"version": version, "revision": "r23630-842932a63d", "target": "mediatek/filogic"},
            "kernel": "5.15.137",
            "rootfs_type": "squashfs",
        },
        "system": {"uptime": up, "load": [0.04, 0.06, 0.01], "memory": {"total": 2.6e8, "available": 1.9e8}},
        "radios": [
            {
                "name": "radio0",
                "up": True,
                "disabled": False,
                "band": "2g",
                "channel": 6,
                "htmode": "HE20",
                "interfaces": [
                    {
                        "section": "default_radio0",
                        "ifname": "phy0-ap0",
                        "ssid": "Castle",
                        "mode": "ap",
                        "clients": [
                            {"mac": "3c:22:fb:10:aa:01", "name": n, "signal": s} for n, s in clients[:1]
                        ],
                    }
                ],
            },
            {
                "name": "radio1",
                "up": True,
                "disabled": False,
                "band": "5g",
                "channel": 36,
                "htmode": "HE80",
                "interfaces": [
                    {
                        "section": "default_radio1",
                        "ifname": "phy1-ap0",
                        "ssid": "Castle",
                        "mode": "ap",
                        "clients": [
                            {"mac": f"3c:22:fb:10:bb:0{i}", "name": n, "signal": s}
                            for i, (n, s) in enumerate(clients[1:])
                        ],
                    },
                    {
                        "section": "mesh1",
                        "ifname": "phy1-mesh0",
                        "ssid": "castle-mesh",
                        "mode": "mesh",
                        "clients": [],
                    },
                ],
            },
        ],
        "services": [
            {"name": "dropbear", "enabled": True, "running": True},
            {"name": "firewall", "enabled": True, "running": True},
            {"name": "network", "enabled": True, "running": True},
            {"name": "openvpn", "enabled": False, "running": False},
            {"name": "rpcd", "enabled": True, "running": True},
            {"name": "uhttpd", "enabled": True, "running": True},
        ],
        "services_allowed": ["dropbear", "openvpn"],
        "packages": ["curl - 8.6.0-1 - 8.6.0-2", "libopenssl3 - 3.0.13-1 - 3.0.15-1"],
        "packages_checked": None,
        "firmware": {
            "current": version,
            "series": version.rsplit(".", 1)[0],
            "supported": False,
            "newest_in_series": "23.05.6",
            "next_step": "23.05.6",
            "stable": "25.12.6",
            "oldstable": "24.10.8",
        },
        "errors": {},
    }


# The OpenVPN servers on the made-up router (its `openvpn` ability), as the hub keeps them.
VPN_SERVERS = {
    "repeater-living": {
        "error": None,
        "servers": {
            "home_udp": {
                "port": 1194,
                "proto": "udp",
                "dev": "tun",
                "running": True,
                "age": 20,
                "connected": [
                    {"name": "phone", "address": "198.51.100.7:51544", "since": "2026-11-21T07:10:00+00:00"}
                ],
            },
            "home_tcp": {
                "port": 443,
                "proto": "tcp-server",
                "dev": "tun",
                "running": True,
                "age": 15,
                "connected": [],
            },
            "old_tap": {
                "port": 1195,
                "proto": "udp",
                "dev": "tap",
                "running": False,
                "age": None,
                "connected": [],
            },
        },
    }
}

# nlbwmon's counters and usteer's view on the made-up access points, as the hub keeps them.
BANDWIDTH = {
    "repeater-living": {
        "error": None,
        "clients": {
            "3c:22:fb:10:aa:01": {
                "name": "Living room TV",
                "rx": 48_200_000_000,
                "tx": 900_000_000,
                "rx_rate": 23_400_000,
                "tx_rate": 410_000,
            },
            "3c:22:fb:10:aa:02": {
                "name": "Pixel 9",
                "rx": 6_100_000_000,
                "tx": 1_200_000_000,
                "rx_rate": 1_200_000,
                "tx_rate": 300_000,
            },
            "3c:22:fb:10:aa:03": {
                "name": "Laptop",
                "rx": 21_000_000_000,
                "tx": 7_400_000_000,
                "rx_rate": 80_000,
                "tx_rate": 20_000,
            },
        },
    }
}
ROAMING = {
    "repeater-living": {
        "error": None,
        "nodes": [
            {
                "node": "hostapd.phy0-ap0",
                "own": True,
                "host": None,
                "iface": "phy0-ap0",
                "bssid": "02:00:00:00:01:01",
                "ssid": "Castle",
                "freq": 2437,
                "clients": 3,
                "load": 12,
                "roam_source": 4,
                "roam_target": 2,
            },
            {
                "node": "hostapd.phy1-ap0",
                "own": True,
                "host": None,
                "iface": "phy1-ap0",
                "bssid": "02:00:00:00:01:02",
                "ssid": "Castle",
                "freq": 5180,
                "clients": 5,
                "load": 20,
                "roam_source": 1,
                "roam_target": 6,
            },
            {
                "node": "192.168.178.4#hostapd.phy1-ap0",
                "own": False,
                "host": "192.168.178.4",
                "iface": "phy1-ap0",
                "bssid": "02:00:00:00:02:02",
                "ssid": "Castle",
                "freq": 5500,
                "clients": 2,
                "load": 8,
                "roam_source": 3,
                "roam_target": 2,
            },
        ],
        "clients": [
            {
                "mac": "3c:22:fb:10:aa:01",
                "node": "hostapd.phy1-ap0",
                "signal": -58,
                "snr": 34,
                "connected": 7_200_000,
            },
            {
                "mac": "3c:22:fb:10:aa:02",
                "node": "hostapd.phy1-ap0",
                "signal": -71,
                "snr": 21,
                "connected": 900_000,
            },
        ],
        "config": {"min_snr": 0, "roam_scan_snr": 15, "roam_trigger_snr": 20, "signal_diff_threshold": 12},
    },
    "repeater-attic": {
        "error": None,
        "nodes": [
            {
                "node": "hostapd.phy1-ap0",
                "own": True,
                "host": None,
                "iface": "phy1-ap0",
                "bssid": "02:00:00:00:02:02",
                "ssid": "Castle",
                "freq": 5500,
                "clients": 2,
                "load": 8,
                "roam_source": 3,
                "roam_target": 2,
            },
            {
                "node": "192.168.178.3#hostapd.phy1-ap0",
                "own": False,
                "host": "192.168.178.3",
                "iface": "phy1-ap0",
                "bssid": "02:00:00:00:01:02",
                "ssid": "Castle",
                "freq": 5180,
                "clients": 5,
                "load": 20,
                "roam_source": 1,
                "roam_target": 6,
            },
        ],
        "clients": [],
        "config": {"min_snr": 0, "roam_scan_snr": 15, "roam_trigger_snr": 25, "signal_diff_threshold": 12},
    },
}

ROUTER = {
    "repeater-living": _router(
        "living",
        "Cudy WR3000 v1",
        "23.05.2",
        [("Kitchen speaker", -61), ("Pixel 9", -48), ("MacBook", -55)],
        9 * 86400,
    ),
    "repeater-attic": _router("attic", "Cudy WR3000 v1", "23.05.2", [("Camera attic", -70)], 9 * 86400),
}


def vitals(id: str, t: float) -> Vitals:
    """The made-up devices' vitals at time t: a daily rhythm, a little noise, steady storage."""
    seed = sum(map(ord, id))
    rnd = random.Random(int(t // 300) * 1000 + seed)  # the same value for the same 5 minutes
    day = math.sin((t / 86400 + seed % 7 / 7) * 2 * math.pi)
    router = id in ROUTER or id in FRITZ
    cpu = max(1.0, (6 if router else 14) + (4 if router else 10) * day + rnd.uniform(-3, 6))
    mem_total = (256 if router else 16384) * 1024 * 1024
    mem = (0.38 if router else 0.52) + 0.06 * day + rnd.uniform(-0.01, 0.01) + seed % 5 / 100
    disk_total = (64 if router else 2000) * 1024 * 1024 * (1 if router else 1024)
    disk = (0.31 if router else 0.58) + seed % 9 / 100 + (t % (30 * 86400)) / (30 * 86400) * 0.02
    return Vitals(
        cpu=round(cpu, 1),
        cores=2 if router else 8,
        memory_used=int(mem * mem_total),
        memory_total=mem_total,
        storage_used=None if id in FRITZ else int(disk * disk_total),
        storage_total=None if id in FRITZ else disk_total,
        storage_mount=None if id in FRITZ else "/",
        temperature=round((52 if router else 44) + 5 * day + rnd.uniform(-1, 1), 1),
    )
