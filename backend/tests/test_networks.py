"""Networks: by address, named in config.yaml, several per device; the gateway and the internet."""

import pytest

from kastellan.config import Config
from kastellan.monitor import CheckStatus
from kastellan.networks import networks_of, public_ways

CONFIG = Config.model_validate(
    {
        "networks": {"flat": {"name": "Holiday flat", "cidr": "10.0.5.0/24"}, "vpn": {"name": "VPN"}},
        "devices": {
            "router": {"host": "192.168.15.254"},
            "nas": {
                "host": "nas.lan",
                "checks": ["ping", {"http": "https://nas.example.org", "name": "public"}],
            },
            "flat-ap": {"host": "10.0.5.1"},
            "vps": {"host": "203.0.113.20"},
            "vpn-server": {"host": "192.168.15.10", "network": ["vpn", "internet"]},
            "phone": {"host": "phone.lan", "network": "vpn"},
        },
    }
)


def nets(id: str, address: str | None, gateway: str | None = "192.168.15.254") -> list[str]:
    return [n.id for n in networks_of(CONFIG, CONFIG.devices[id], address, gateway)]


def test_by_address_and_by_name() -> None:
    assert nets("nas", "192.168.15.20") == ["net-192.168.15.0"]
    assert nets("flat-ap", "10.0.5.1") == ["flat"]  # named in networks:
    assert nets("vps", "203.0.113.20") == ["internet"]
    assert nets("nas", None) == []  # unknown (the API then assumes the hub's own)


def test_on_the_boundary() -> None:
    assert nets("router", "192.168.15.254") == ["net-192.168.15.0", "internet"]  # the gateway
    assert nets("router", "192.168.15.254", gateway=None) == ["net-192.168.15.0"]
    assert nets("vpn-server", "192.168.15.10") == ["vpn", "internet"]  # as config.yaml says
    assert nets("phone", None) == ["vpn"]  # a single name works too


def test_ways_in_from_the_internet() -> None:
    nas = CONFIG.devices["nas"]
    ways = public_ways(nas, "192.168.15.20", {"public": CheckStatus(up=True, address="198.51.100.7")})
    assert ways == [{"check": "public", "target": "https://nas.example.org", "address": "198.51.100.7"}]
    vps = CONFIG.devices["vps"]
    assert public_ways(vps, "203.0.113.20", {}) == []  # a ping is no way in


def test_unknown_network_names_are_refused() -> None:
    with pytest.raises(ValueError, match="not in networks"):
        Config.model_validate({"devices": {"x": {"host": "h", "network": "nowhere"}}})
    with pytest.raises(ValueError, match="cidr"):
        Config.model_validate({"networks": {"a": {"cidr": "not a net"}}})
