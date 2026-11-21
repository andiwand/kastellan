from datetime import UTC, datetime
from pathlib import Path

import pytest

from kastellan.secrets import Secrets
from kastellan.vpn.openvpn import parse_list
from kastellan.vpn.wireguard import _client, _id


def test_secrets_are_private_files(tmp_path: Path) -> None:
    secrets = Secrets(tmp_path / "secrets")
    secrets.set("wg-easy", "pa$$word\n")
    assert secrets.get("wg-easy") == "pa$$word"
    assert (tmp_path / "secrets" / "wg-easy").stat().st_mode & 0o077 == 0
    assert (tmp_path / "secrets").stat().st_mode & 0o077 == 0
    assert secrets.names() == ["wg-easy"]
    with pytest.raises(KeyError):
        secrets.get("missing")
    with pytest.raises(ValueError):
        secrets.get("../config.yaml")


def test_openvpn_list() -> None:
    # As kastellan-pki prints it (easy-rsa 3.2.7's index.txt, checked against a real PKI).
    text = (
        "V\t261220160426Z\tF931\talice\n"
        "R\t261220160426Z\tA1B2\tbob\n"
        "V\t240101000000Z\tC3D4\told\n"
        "garbage line\n"
    )
    clients = {c.name: c for c in parse_list(text, now=datetime(2026, 11, 20, tzinfo=UTC))}
    assert set(clients) == {"alice", "bob", "old"}  # (kastellan-pki lists client certificates only)
    assert (clients["alice"].status, clients["alice"].expires) == ("valid", "2026-12-20")
    assert clients["bob"].status == "revoked" and clients["old"].status == "expired"


def test_wg_easy_client() -> None:
    # Fields as wg-easy 15.5's GET /api/client returns them.
    c = _client(
        {
            "id": 3,
            "name": "phone",
            "enabled": False,
            "ipv4Address": "10.8.0.4",
            "latestHandshakeAt": None,
            "transferRx": 1024,
            "transferTx": None,
        }
    )
    assert (c.id, c.name, c.enabled, c.address, c.rx, c.tx) == ("3", "phone", False, "10.8.0.4", 1024, None)
    assert _id("12") == "12"
    with pytest.raises(ValueError):
        _id("1/../../setup")  # nothing but digits goes into a URL path


def test_openvpn_status_versions_and_last_seen(tmp_path: Path) -> None:
    from kastellan.vpn.openvpn import Seen, parse_status

    v3 = (
        "== server\n"
        "HEADER\tCLIENT_LIST\tCommon Name\tReal Address\tVirtual Address\tVirtual IPv6 Address"
        "\tBytes Received\tBytes Sent\tConnected Since\tConnected Since (time_t)\tUsername\n"
        "CLIENT_LIST\tcarol\t192.0.2.9:1194\t10.8.0.3\t\t7\t8\tsomething\t1795246400\tUNDEF\n"
        "CLIENT_LIST\tUNDEF\t192.0.2.10:1194\t\t\t0\t0\tsomething\t1795246400\tUNDEF\n"  # not through yet
        "CLIENT_LIST\t../etc\t192.0.2.11:1194\t\t\t0\t0\tx\t1\tUNDEF\n"  # not a client name
        "== other\n!no-status\n"
    )
    found = parse_status(v3)
    assert [(c.name, c.rx, c.tx, c.since) for c in found] == [("carol", 7, 8, "2026-11-21T07:33:20+00:00")]

    seen = Seen(tmp_path / "vpn")
    seen.note(found, datetime(2026, 11, 21, 10, tzinfo=UTC))
    seen.note([], datetime(2026, 11, 21, 11, tzinfo=UTC))  # gone: the last time stays
    again = Seen(tmp_path / "vpn")
    assert again.last == {"carol": "2026-11-21T10:00:00+00:00"}
