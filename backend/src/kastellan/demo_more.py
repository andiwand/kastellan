"""Demo mode's made-up logs, device backups, FRITZ!Box, internet line and Wi-Fi scans (see
demo.py): seeded once at start into the demo's temporary folder, and a little more as time goes
on, so every page has something true-looking to show."""

from __future__ import annotations

import hashlib
import io
import math
import random
import tarfile
from datetime import datetime
from typing import Any

from . import devbackups, internet
from .logs import Line

# --- The FRITZ!Box ----------------------------------------------------------------------------

FRITZ = {
    "fritzbox": {
        "model": "FRITZ!Box 7590",
        "firmware": "8.20",
        "uptime": 23 * 86400,
        "update": {"available": True, "version": "FRITZ!OS 8.21"},
        "internet": {
            "access": "DSL",
            "link": "Up",
            "max_up": 46_720_000,
            "max_down": 250_000_000,
            "connection": "Connected",
            "connected_for": 3 * 86400 + 4000,
            "address": "203.0.113.48",
            "address_v6": "2001:db8:4a1::1",
            "via": "WANPPPConnection",
            "dsl": {
                "status": "Up",
                "up": 46_720,
                "down": 250_000,
                "max_up": 52_000,
                "max_down": 289_000,
                "snr_up": 82,
                "snr_down": 61,
                "crc": 112,
                "fec": 48_211,
                "errored_seconds": 37,
            },
            "rate": {"down": 18_400_000, "up": 1_200_000, "at": 0},
        },
        "wifi": [
            {
                "index": 1,
                "ssid": "Castle",
                "enabled": True,
                "status": "Up",
                "channel": 6,
                "band": "2g",
                "possible_channels": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
                "standard": "ax",
                "encryption": "11iandWPA3",
                "guest": False,
                "clients": 4,
                "bssid": "dc:39:6f:00:00:01",
                "wps": True,
            },
            {
                "index": 2,
                "ssid": "Castle",
                "enabled": True,
                "status": "Up",
                "channel": 44,
                "band": "5g",
                "possible_channels": [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128],
                "standard": "ax",
                "encryption": "11iandWPA3",
                "guest": False,
                "clients": 9,
                "bssid": "dc:39:6f:00:00:02",
            },
            {
                "index": 3,
                "ssid": "Castle guests",
                "enabled": True,
                "status": "Up",
                "channel": 6,
                "band": "2g",
                "possible_channels": [],
                "standard": "ax",
                "encryption": "11i",
                "guest": True,
                "clients": 1,
                "bssid": "dc:39:6f:00:00:03",
            },
        ],
        "clients": [
            {
                "name": "Pixel-9",
                "mac": "3c:22:fb:10:bb:00",
                "address": "192.168.178.40",
                "via": "802.11",
                "speed": 1200,
                "guest": False,
            },
            {
                "name": "MacBook",
                "mac": "3c:22:fb:10:bb:01",
                "address": "192.168.178.41",
                "via": "802.11",
                "speed": 866,
                "guest": False,
            },
            {
                "name": "nas",
                "mac": "00:11:32:aa:bb:cc",
                "address": "192.168.178.10",
                "via": "Ethernet",
                "speed": 1000,
                "guest": False,
            },
            {
                "name": "Visitor phone",
                "mac": "aa:bb:cc:11:22:33",
                "address": "192.168.179.20",
                "via": "802.11",
                "speed": 144,
                "guest": True,
            },
        ],
        "forwards": [
            {
                "enabled": True,
                "protocol": "UDP",
                "port": 51820,
                "to": "192.168.178.10",
                "to_port": 51820,
                "name": "WireGuard",
            },
            {
                "enabled": False,
                "protocol": "TCP",
                "port": 8443,
                "to": "192.168.178.11",
                "to_port": 443,
                "name": "old web server",
            },
        ],
        "remote": {"remote_access": False, "myfritz": True},
        "mesh": {
            "members": [
                {
                    "uid": "n1",
                    "name": "fritz.box",
                    "model": "FRITZ!Box 7590",
                    "role": "master",
                    "mac": None,
                    "firmware": "8.20",
                },
                {
                    "uid": "n2",
                    "name": "Repeater hall",
                    "model": "FRITZ!Repeater 1200 AX",
                    "role": "repeater",
                    "mac": None,
                    "firmware": "8.20",
                },
            ],
            "links": [
                {"a": "n1", "b": "n2", "type": "WLAN", "rate_rx": 1201000, "rate_tx": 960000, "signal": -58}
            ],
        },
        "wireguard": {
            "server": "abc123.myfritz.net",
            "port": "51820",
            "address": "192.168.178.1",
            "connections": [
                {
                    "kind": "device",
                    "name": "Ada's phone",
                    "active": True,
                    "status": "connected",
                    "last_handshake": "2 min ago",
                    "endpoint": None,
                    "networks": None,
                },
            ],
            "error": None,
        },
        "errors": {},
    }
}


# --- Wi-Fi ------------------------------------------------------------------------------------


def _entry(device: str, section: str, band: str, key: str = "k1") -> dict[str, Any]:
    return {
        "device": device,
        "section": section,
        "radio": "radio0" if band == "2g" else "radio1",
        "band": band,
        "encryption": "sae-mixed",
        "key": key,
        "disabled": False,
    }


WIFI_SURVEY = {
    "members": ["repeater-attic", "repeater-living"],
    "errors": {},
    "networks": [
        {
            "ssid": "Castle",
            "on": [
                _entry(d, f"default_radio{r}", b, "k1")
                for d in ("repeater-attic", "repeater-living")
                for r, b in ((0, "2g"), (1, "5g"))
            ],
        },
        {"ssid": "Castle guests", "on": [_entry("repeater-living", "guest", "2g")]},
    ],
    "mesh": [],
}

NEIGHBOURS = [
    ("FRITZ!Box 6660 Cable", 1, 20, -67),
    ("Vodafone-4A2C", 6, 20, -58),
    ("Telekom-8812", 6, 40, -71),
    ("DIRECT-HP-Printer", 11, 20, -80),
    ("o2-WLAN42", 1, 20, -74),
    ("Mueller", 6, 20, -62),
]
NEIGHBOURS_5 = [("Vodafone-4A2C", 36, 80, -66), ("Telekom-8812", 100, 80, -78), ("Mueller", 36, 80, -73)]


def scans(now: float) -> list[dict[str, Any]]:
    """A scan from each repeater radio an hour ago: the 2.4 GHz band is crowded on 6, the two
    repeaters share 6 and 36, and hear each other well."""
    out = []
    for device, offset in (("repeater-living", 0), ("repeater-attic", -9)):
        other = "repeater-attic" if device == "repeater-living" else "repeater-living"
        for radio, band, channel, near in (
            ("radio0", "2g", 6, NEIGHBOURS),
            ("radio1", "5g", 36, NEIGHBOURS_5),
        ):
            mac = f"94:83:c4:{'01' if device == 'repeater-living' else '02'}:{radio[-1]}0:01"
            other_mac = f"94:83:c4:{'02' if device == 'repeater-living' else '01'}:{radio[-1]}0:01"
            neighbours = [
                {
                    "ssid": s,
                    "bssid": f"aa:00:{i:02x}:{band[0]}0:00:01",
                    "channel": c,
                    "band": band,
                    "signal": sig + offset,
                    "width": w,
                    "encrypted": True,
                }
                for i, (s, c, w, sig) in enumerate(near)
            ]
            neighbours.append(
                {
                    "ssid": "Castle",
                    "bssid": other_mac,
                    "channel": channel,
                    "band": band,
                    "signal": -61 if band == "2g" else -69,
                    "width": 20 if band == "2g" else 80,
                    "encrypted": True,
                }
            )
            busy = (
                {2412: 31, 2437: 64, 2462: 22}
                if band == "2g"
                else {5180: 38, 5200: 33, 5220: 31, 5240: 30, 5500: 9, 5260: 12}
            )
            out.append(
                {
                    "t": now - 3600 + (60 if other == "repeater-living" else 0),
                    "device": device,
                    "radio": radio,
                    "band": band,
                    "channel": channel,
                    "neighbours": neighbours,
                    "survey": [{"mhz": m, "busy": b, "noise": -92, "in_use": False} for m, b in busy.items()],
                    "freqs": [
                        {"channel": c, "mhz": 0, "restricted": c in range(52, 145), "active": False}
                        for c in (
                            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
                            if band == "2g"
                            else [
                                36,
                                40,
                                44,
                                48,
                                52,
                                56,
                                60,
                                64,
                                100,
                                104,
                                108,
                                112,
                                116,
                                120,
                                124,
                                128,
                                132,
                                136,
                                140,
                            ]
                        )
                    ],
                    "bssids": [mac],
                }
            )
    return out


# --- Logs -------------------------------------------------------------------------------------

LOG_STORY = [
    (
        "repeater-living",
        "syslog",
        "dropbear",
        6,
        "Password auth succeeded for 'root' from 192.168.178.41:52344",
    ),
    ("repeater-living", "syslog", "dropbear", 4, "Bad password attempt for 'root' from 192.168.178.66:40112"),
    ("repeater-living", "syslog", "hostapd", 6, "phy1-ap0: AP-STA-CONNECTED 3c:22:fb:10:bb:00 auth_alg=sae"),
    ("repeater-living", "syslog", "hostapd", 6, "phy0-ap0: AP-STA-DISCONNECTED 3c:22:fb:10:aa:01"),
    ("repeater-living", "kernel", "kernel", 6, "br-lan: port 3(phy1-mesh0) entered forwarding state"),
    ("repeater-attic", "syslog", "hostapd", 6, "phy1-ap0: AP-STA-CONNECTED 5c:aa:fd:00:11:22 auth_alg=sae"),
    ("repeater-attic", "syslog", "netifd", 5, "Network device 'phy1-mesh0' link is up"),
    (
        "repeater-attic",
        "syslog",
        "dnsmasq-dhcp",
        6,
        "DHCPACK(br-lan) 192.168.178.52 5c:aa:fd:00:11:22 camera-attic",
    ),
    (
        "fritzbox",
        "fritzbox",
        "fritzbox",
        6,
        "Internetverbindung wurde erfolgreich hergestellt. IP-Adresse: 203.0.113.48",
    ),
    (
        "fritzbox",
        "fritzbox",
        "fritzbox",
        4,
        "Anmeldung der Benutzerin admin an der FRITZ!Box-Benutzeroberfläche "
        "fehlgeschlagen (IP-Adresse: 192.168.178.66).",
    ),
    (
        "fritzbox",
        "fritzbox",
        "fritzbox",
        6,
        "WLAN-Gerät angemeldet (5 GHz), 866 Mbit/s, Pixel-9, IP 192.168.178.40",
    ),
    ("fritzbox", "fritzbox", "fritzbox", 6, "VPN-Verbindung zu wg-phone wurde erfolgreich hergestellt."),
    ("nas", "syslog", "sshd", 6, "Accepted publickey for admin from 192.168.178.41 port 50122 ssh2"),
    ("nas", "syslog", "sshd", 4, "Failed password for invalid user test from 192.168.178.66 port 41022 ssh2"),
    ("nas", "syslog", "openvpn", 6, "phone/198.51.100.7:51544 Peer Connection Initiated"),
    ("nas", "syslog", "sshd", 5, "Invalid user pi from 203.0.113.77 port 51234"),
    (
        "nas",
        "syslog",
        "sshd",
        6,
        "Connection closed by authenticating user root 203.0.113.77 port 40112 [preauth]",
    ),
    (
        "repeater-living",
        "syslog",
        "openvpn(home_tcp)",
        4,
        "tcp4-server:198.51.100.200:40000 TLS Error: cannot locate HMAC in incoming packet from "
        "[AF_INET]198.51.100.200:40000",
    ),
    ("nas", "syslog", "kernel", 3, "ata2.00: failed command: READ FPDMA QUEUED"),
]


def log_lines(since: float, until: float, seed: int = 7) -> list[Line]:
    """Lines spread over a time span, the story's in a random order and pace."""
    rng = random.Random(seed)
    out = []
    t = since
    while t < until:
        t += rng.uniform(60, 900)
        device, source, program, level, msg = rng.choice(LOG_STORY)
        out.append(
            Line(min(t, until), device, source, program, level, msg, source != "syslog" or device != "nas")
        )
    return out


# --- Device backups ---------------------------------------------------------------------------

UCI_WIRELESS = """config wifi-device 'radio0'
\toption type 'mac80211'
\toption band '2g'
\toption channel '{channel}'
\toption htmode 'HE20'

config wifi-iface 'default_radio0'
\toption device 'radio0'
\toption network 'lan'
\toption mode 'ap'
\toption ssid 'Castle'
\toption encryption 'sae-mixed'
\toption key '{key}'
"""


def _tar(files: dict[str, str]) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o600, 1_700_000_000
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def seed_backups(store: devbackups.Store) -> None:
    """Three versions of the living-room repeater (a channel change, then a new password) and
    one FRITZ!Box export taken twice the same."""
    base = {
        "etc/config/system": "config system\n\toption hostname 'living'\n",
        "etc/shadow": "root:$1$abc$xyz:19000:0:99999:7:::\n",
        "etc/dropbear/dropbear_ed25519_host_key": "not really a key\n",
    }
    versions = [
        base | {"etc/config/wireless": UCI_WIRELESS.format(channel="1", key="old-castle-key")},
        base | {"etc/config/wireless": UCI_WIRELESS.format(channel="6", key="old-castle-key")},
        base | {"etc/config/wireless": UCI_WIRELESS.format(channel="6", key="new-castle-key")},
    ]
    for files in versions:
        store.add("repeater-living", "openwrt-sysupgrade", devbackups.unpack_tar(_tar(files)), 60)
    store.add("repeater-attic", "openwrt-sysupgrade", devbackups.unpack_tar(_tar(versions[1])), 60)
    for salt in ("$$$$AAAA", "$$$$BBBB"):  # two exports: only the encryption differs
        text = (
            f"**** FRITZ!Box 7590 CONFIGURATION EXPORT\nPassword={salt}\nFirmwareVersion=154.08.20\n"
            f'**** CFGFILE:ar7.cfg\nwlan {{ ssid = "Castle"; key = "{salt}123"; }}\n'
            "**** END OF EXPORT 1A2B3C4D ****\n"
        )
        content = text.encode()
        sha = hashlib.sha256(content).hexdigest()
        same = sha
        f = devbackups.File("fritzbox.export", sha, len(content), "file", 0o600, 0, "", same)
        store.add("fritzbox", "fritzbox-export", [(f, content)], 60)


# --- The internet line ------------------------------------------------------------------------


def sample(t: float) -> internet.Sample:
    """The line at time t: a short outage at the provider the day before yesterday at 03:12, a
    slow evening now and then."""
    rng = random.Random(int(t) // 30)
    hour = datetime.fromtimestamp(t).hour + datetime.fromtimestamp(t).minute / 60
    evening = 1 + 0.6 * max(0.0, math.sin((hour - 15) / 9 * math.pi)) if 15 <= hour <= 24 else 1
    day = datetime.fromtimestamp(t).astimezone().date()
    today = datetime.now().astimezone().date()
    if (today - day).days == 2 and 3.2 <= hour < 3.45:
        return internet.Sample(t, "down", "provider", router_ms=0.6, line=True, loss=1.0, hop_loss=1.0)
    ms = 11.5 * evening * rng.uniform(0.9, 1.2)
    loss = 0.0 if rng.random() > 0.05 else 0.02
    down = 8e6 + 40e6 * (evening - 1) * rng.uniform(0.3, 1.3) + rng.uniform(0, 6e6)
    return internet.Sample(
        t,
        "up",
        None,
        router_ms=round(0.6 * rng.uniform(0.8, 1.4), 2),
        line=True,
        loss=loss,
        ms=round(ms, 1),
        jitter=round(rng.uniform(0.3, 2.5), 1),
        v6_loss=0.0,
        v6_ms=round(ms * 0.95, 1),
        dns_ms=round(rng.uniform(4, 16), 1),
        hop_ms=round(ms * 0.55, 1),
        hop_loss=0.0,
        down=round(down),
        up=round(down * 0.08),
    )


LINE = {
    "address": "203.0.113.48",
    "address_v6": "2001:db8:4a1::1",
    "connected_for": 3 * 86400 + 4000,
    "access": "DSL",
    "dsl": FRITZ["fritzbox"]["internet"]["dsl"],
    "max_down": 250_000_000,
    "max_up": 46_720_000,
}


def seed_internet(net: internet.Internet, now: float) -> None:
    """A week of looks every five minutes (the samples' story above), a reconnect, speed tests."""
    net.router = "fritzbox"
    net.route = [
        {"distance": 1, "address": "192.168.178.1", "ms": 0.6},
        {"distance": 2, "address": "100.70.12.1", "ms": 6.4},
        {"distance": 3, "address": "62.155.240.17", "ms": 7.9},
        {"distance": 4, "address": "1.1.1.1", "ms": 11.2},
    ]
    net.hops, net.route_checked = ["100.70.12.1", "62.155.240.17"], now
    t = now - 7 * 86400
    while t < now:
        s = sample(t)
        net.note(s, LINE | {"connected_for": None}, 300)
        t += 300
    for i, (down, up) in enumerate([(231e6, 41e6), (244e6, 43e6), (189e6, 40e6), (236e6, 42e6)]):
        net.note_speedtest(
            {
                "t": now - (4 - i) * 86400 + 4 * 3600,
                "down": down,
                "up": up,
                "ms_idle": 11.2,
                "ms_loaded": 38.0 + i * 3,
                "server": "speed.cloudflare.com",
                "by": None,
            }
        )


def demo_key() -> bytes:
    """The demo's backups' key: made up, never a real secret."""
    return hashlib.sha256(b"kastellan demo backups").digest()
