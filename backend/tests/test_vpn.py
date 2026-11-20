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
        "V\t351231235959Z\t0001\tserver\n"
        "garbage line\n"
    )
    clients = {c.name: c for c in parse_list(text, now=datetime(2026, 11, 20, tzinfo=UTC))}
    assert set(clients) == {"alice", "bob", "old"}  # the server's own certificate isn't a client
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
