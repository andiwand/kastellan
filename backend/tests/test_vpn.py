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


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("!no-servers\n", "no OpenVPN server found on gw"),
        ("== server\n!no-status\n", "keeps no status file"),
        ("== server\n!unreadable\n", "can't be read"),
        ("== server\n", None),  # a server nobody is connected to
    ],
)
async def test_the_hub_says_why_nobody_is_seen(tmp_path: Path, monkeypatch, text: str, error) -> None:
    from types import SimpleNamespace

    from kastellan import hub as hub_module
    from kastellan.vpn.openvpn import Seen

    async def status(ubus) -> str:
        return text

    monkeypatch.setattr(hub_module.openwrt_client, "openvpn_status", status)
    seen = Seen(tmp_path, "office")
    me = SimpleNamespace(
        seen=lambda vid: seen,
        ovpn_status={"office": {"connected": [{"name": "old"}]}},
        config=SimpleNamespace(vpns={"office": SimpleNamespace(instance=None)}),
    )
    await hub_module.Hub._read_openvpn(me, ["office"], "gw", None)  # type: ignore[arg-type]
    status = me.ovpn_status["office"]
    assert status["connected"] == []
    assert (error is None and status["error"] is None) or error in status["error"]

    async def broken(ubus) -> str:
        raise RuntimeError("Access denied")

    monkeypatch.setattr(hub_module.openwrt_client, "openvpn_status", broken)
    me.ovpn_status = {"office": {"checked": "x", "connected": [{"name": "old"}], "error": None}}
    await hub_module.Hub._read_openvpn(me, ["office"], "gw", None)  # type: ignore[arg-type]
    assert me.ovpn_status["office"]["connected"] == [] and me.ovpn_status["office"]["error"]


def test_setting_a_vpn_up_again_replaces_its_old_key_line(tmp_path) -> None:
    """The first line matching a key counts: an older VPN's line for the same script would win."""
    import subprocess

    from kastellan import access

    k, other = "AAAAC3NzaC1lZDI1NTE5AAAAIK" + "k" * 42, "AAAAC3NzaC1lZDI1NTE5AAAAIO" + "o" * 42
    pki = "/usr/local/bin/kastellan-pki"
    old = f'from="10.0.0.1",restrict,command="{pki} /etc/kastellan-pki.conf" ssh-ed25519 {k}'
    new = f'restrict,command="/usr/local/bin/kastellan-pki /etc/kastellan-pki-home.conf" ssh-ed25519 {k}'
    crl = f'command="/usr/bin/kastellan-crl /etc/kastellan-crl.conf",no-pty ssh-ed25519 {k}'
    near = f'command="/usr/local/bin/kastellan-pkix x" ssh-ed25519 {k}'  # another script
    mine = f"ssh-ed25519 {other} ada@laptop"
    f = tmp_path / "authorized_keys"
    f.write_text("\n".join([old, crl, near, mine, new]) + "\n")
    subprocess.run(["sh", "-c", access._DEDUPE, str(f), new, "/usr/local/bin/kastellan-pki", k], check=True)
    assert f.read_text() == "\n".join([crl, near, mine, new]) + "\n"
    script = "\n".join(
        access.authorize("kastellan-pki", "OPEN", "DROP", replace=("/usr/local/bin/kastellan-pki", k))
    )
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0


def test_the_daily_crl_renewal_survives_a_vpn_removed_meanwhile(tmp_path, monkeypatch) -> None:
    """A VPN removed while another's CRL was pushed: skipped, and the loop goes on for all."""
    import asyncio
    import contextlib

    from fastapi.testclient import TestClient

    from kastellan import hub as hub_module
    from kastellan.api import create_app
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  sv1: {host: h}\n  gw: {host: g}\n"
        "vpns:\n  a: {kind: openvpn, pki: sv1, server: gw}\n"
        "  b: {kind: openvpn, pki: sv1, server: gw, pki_user: b-pki, server_user: b-crl}\n"
        "  c: {kind: openvpn, pki: sv1, server: gw, pki_user: c-pki, server_user: c-crl}\n"
    )
    with TestClient(create_app(Settings(config=config, data=tmp_path / "data"))) as c:
        hub = c.app.state.hub
        pushed: list[str] = []

        async def push(vid: str, renew: bool = False) -> None:
            pushed.append(vid)
            if vid == "a":  # meanwhile, an admin removes b
                hub.config.vpns.pop("b")
            if vid == "c":
                raise RuntimeError("the server didn't answer")

        sleeps = iter([None])

        async def sleep(seconds: float) -> None:
            if next(sleeps, "stop") == "stop":
                raise asyncio.CancelledError

        monkeypatch.setattr(hub, "push_crl", push)
        monkeypatch.setattr(hub_module.asyncio, "sleep", sleep)
        sent = []

        async def send(messages, now) -> None:
            sent.extend(messages)

        monkeypatch.setattr(hub, "_send", send)
        with contextlib.suppress(asyncio.CancelledError):
            asyncio.run(hub._renew_crl())
        assert pushed == ["a", "c"]  # b skipped once gone; c's failure didn't end the loop early
        assert [m.title for m in sent] == ["c: the CRL couldn't be renewed"]


async def test_several_openvpn_servers_on_one_router_are_kept_apart(tmp_path: Path, monkeypatch) -> None:
    """Each VPN counts only its own server's clients: by `instance:`, else none when it can't
    tell (no guessing between servers)."""
    from types import SimpleNamespace

    from kastellan import hub as hub_module
    from kastellan.vpn.openvpn import Seen

    text = (
        "== office\nCommon Name,Real Address,Bytes Received,Bytes Sent,Connected Since\n"
        "laptop,198.51.100.7:40000,5,6,2026-11-21 08:00:00\n"
        "== lab.conf\n!unreadable\n"
    )

    async def status(ubus) -> str:
        return text

    monkeypatch.setattr(hub_module.openwrt_client, "openvpn_status", status)
    seen = {v: Seen(tmp_path, v) for v in ("a", "b", "c")}
    vpns = {
        "a": SimpleNamespace(instance="office"),
        "b": SimpleNamespace(instance="lab.conf"),
        "c": SimpleNamespace(instance=None),
    }
    me = SimpleNamespace(seen=lambda v: seen[v], ovpn_status={}, config=SimpleNamespace(vpns=vpns))
    await hub_module.Hub._read_openvpn(me, ["a", "b", "c"], "gw", None)  # type: ignore[arg-type]
    a, b, c = (me.ovpn_status[v] for v in ("a", "b", "c"))
    assert [x["name"] for x in a["connected"]] == ["laptop"] and a["error"] is None
    assert b["connected"] == [] and "can't be read" in b["error"]  # its own error only
    assert c["connected"] == [] and "choose its instance" in c["error"]
    assert "laptop" in seen["a"].last and not seen["b"].last and not seen["c"].last
