from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kastellan.secrets import Secrets
from kastellan.vpn.openvpn import parse_list
from kastellan.vpn.wireguard import _client, _id


def test_crl_installs_verify_signatures_and_refuse_rollback(tmp_path: Path) -> None:
    import os
    import shlex
    import subprocess

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from kastellan.vpn.openvpn import script

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test CA")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca_path, crl_path = tmp_path / "ca.pem", tmp_path / "crl.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    conf = tmp_path / "crl.conf"
    conf.write_text(f"CA={shlex.quote(str(ca_path))}\nCRL={shlex.quote(str(crl_path))}\n")
    executable = tmp_path / "kastellan-crl"
    executable.write_text(script("kastellan-crl"))

    def crl(number, signing_key=key, issued=now):
        builder = (
            x509.CertificateRevocationListBuilder()
            .issuer_name(issuer)
            .last_update(issued)
            .next_update(now + timedelta(days=1))
        )
        if number is not None:
            builder = builder.add_extension(x509.CRLNumber(number), critical=False)
        return builder.sign(signing_key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM)

    def install(pem):
        return subprocess.run(
            ["sh", str(executable), str(conf)],
            input=pem,
            capture_output=True,
            env={**os.environ, "SSH_ORIGINAL_COMMAND": "install"},
        )

    for number in (255, 256, 2**100):
        current = crl(number)
        result = install(current)
        assert result.returncode == 0, result.stderr
        result = install(crl(number - 1))
        assert result.returncode != 0 and b"older" in result.stderr
        assert crl_path.read_bytes() == current
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    result = install(crl(2**100 + 1, other))
    assert result.returncode != 0 and b"not signed" in result.stderr
    assert crl_path.read_bytes() == current
    current = crl(None)
    assert install(current).returncode == 0
    assert install(crl(None, issued=now - timedelta(days=1))).returncode != 0
    assert crl_path.read_bytes() == current


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
    from kastellan.config import OpenVpnSource
    from kastellan.vpn.openvpn import Seen

    async def status(ubus) -> str:
        return text

    monkeypatch.setattr(hub_module.openwrt_client, "openvpn_status", status)
    seen = Seen(tmp_path, "office")
    me = SimpleNamespace(
        seen=lambda vid: seen,
        ovpn_status={"office": {"connected": [{"name": "old"}]}},
        vpn_servers={},
        config=SimpleNamespace(vpns={"office": OpenVpnSource(pki="pki", server="gw")}),
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

    from kastellan import hub as hub_module
    from kastellan.config import load
    from kastellan.settings import Settings

    config = tmp_path / "config.yaml"
    config.write_text(
        "devices:\n  sv1: {host: h}\n  gw: {host: g}\n"
        "vpns:\n  a: {kind: openvpn, pki: sv1, server: gw}\n"
        "  b: {kind: openvpn, pki: sv1, server: gw, pki_user: b-pki, server_user: b-crl}\n"
        "  c: {kind: openvpn, pki: sv1, server: gw, pki_user: c-pki, server_user: c-crl}\n"
    )
    hub = hub_module.Hub(Settings(config=config, data=tmp_path / "data"))
    hub.config = load(config)
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
    from kastellan.config import OpenVpnSource
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
        "a": OpenVpnSource(pki="pki", server="gw", instance="office"),
        "b": OpenVpnSource(pki="pki", server="gw", instance="lab.conf"),
        "c": OpenVpnSource(pki="pki", server="gw"),
    }
    me = SimpleNamespace(
        seen=lambda v: seen[v], ovpn_status={}, vpn_servers={}, config=SimpleNamespace(vpns=vpns)
    )
    await hub_module.Hub._read_openvpn(me, ["a", "b", "c"], "gw", None)  # type: ignore[arg-type]
    a, b, c = (me.ovpn_status[v] for v in ("a", "b", "c"))
    assert [x["name"] for x in a["connected"]] == ["laptop"] and a["error"] is None
    assert b["connected"] == [] and "can't be read" in b["error"]  # its own error only
    assert c["connected"] == [] and "choose its instance" in c["error"]
    assert "laptop" in seen["a"].last and not seen["b"].last and not seen["c"].last


def test_vpn_servers_are_listed_from_what_the_hub_has() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from kastellan import vpn_servers
    from kastellan.config import Config
    from kastellan.networks import Resolver

    config = Config.model_validate(
        {
            "devices": {
                "gw": {"host": "192.168.1.1", "kind": "router"},
                "sv": {"host": "192.168.1.10", "kind": "server"},
            },
            "vpns": {
                "home": {"kind": "openvpn", "pki": "sv", "server": "gw", "instance": "home_udp"},
                "wg": {"kind": "wireguard", "wg_easy": "http://192.168.1.10:51821"},
            },
        }
    )
    now = datetime.now(UTC)
    status = {
        "gw": SimpleNamespace(
            router={"interfaces": [{"name": "wg0", "proto": "wireguard", "up": True, "ipv4": ["10.0.0.1"]}]},
            router_checked=now,
            fritz=None,
            containers=None,
            containers_checked=None,
        ),
        "sv": SimpleNamespace(
            router=None,
            fritz=None,
            containers=[
                {"name": "wg-easy", "image": "wg-easy/wg-easy", "tag": "15", "state": "running"},
                {"name": "web", "image": "library/nginx", "tag": "1", "state": "running"},
            ],
            containers_checked=now,
        ),
    }
    resolver = Resolver()
    resolver.addresses = {"wg.example": "192.168.1.10", "localhost": "127.0.0.1"}
    hub = SimpleNamespace(
        config=config,
        monitor=SimpleNamespace(status=status),
        resolver=resolver,
        vpn_servers={
            "gw": {
                "checked": "x",
                "servers": {
                    "home_udp": {
                        "port": 1194,
                        "running": True,
                        "age": 10,
                        "connected": [{"name": "phone", "address": "198.51.100.7:1", "since": None}],
                    },
                    "old.conf": {"port": None, "running": None, "age": 9999, "connected": []},
                },
            }
        },
    )
    servers = vpn_servers.collect(hub, admin=False)  # type: ignore[arg-type]
    by = {(s["device"], s["name"]): s for s in servers}
    home = by[("gw", "home_udp")]
    assert home["vpn"] == "home" and home["running"] and home["clients"] == [{"name": "phone", "since": None}]
    assert by[("gw", "old.conf")]["running"] is False and by[("gw", "old.conf")]["stale"]
    assert by[("gw", "old.conf")]["clients"] is None
    assert by[("gw", "wg0")]["kind"] == "wireguard" and by[("gw", "wg0")]["addresses"] is None
    assert by[("sv", "wg-easy")]["vpn"] == "wg" and ("sv", "web") not in by
    admin = vpn_servers.collect(hub, admin=True)  # type: ignore[arg-type]
    assert next(s for s in admin if s["name"] == "home_udp")["clients"][0]["address"] == "198.51.100.7:1"
    # The servers whose clients Kastellan manages come first.
    flags = [s["vpn"] is not None for s in servers]
    assert flags == sorted(flags, reverse=True)
    # wg-easy behind a reverse proxy on the same host, by a name of its own: the same server.
    config.vpns["wg"].wg_easy = "https://wg.example"
    assert vpn_servers.wg_easy_hosts(config) == {"wg.example"}
    again = vpn_servers.collect(hub, True)  # type: ignore[arg-type]
    assert next(s for s in again if s.get("device") == "sv")["vpn"] == "wg"
    assert not any(s["source"] == "wg-easy" for s in again)
    config.vpns["wg"].wg_easy = "http://localhost:51821"
    again = vpn_servers.collect(hub, True)  # type: ignore[arg-type]
    assert next(s for s in again if s.get("device") == "sv")["vpn"] is None
    managed = [s["source"] for s in again if s["vpn"]]
    assert "wg-easy" in managed and again[: len(managed)] == [s for s in again if s["vpn"]]


def test_wireguard_rates_from_wg_easys_totals() -> None:
    from kastellan.vpn.wireguard import Rates

    rates = Rates()
    assert rates.update("wg/1", 1000, 5000, now=100.0) is None  # one read: no rate yet
    assert rates.update("wg/1", 3000, 25000, now=110.0) == (200.0, 2000.0)
    assert rates.update("wg/1", 3100, 25100, now=111.0) == (200.0, 2000.0)  # too soon: kept
    assert rates.update("wg/1", 100, 100, now=130.0) is None  # the interface restarted
    assert rates.update("wg/1", 100, 100, now=1000.0) is None  # too long ago to be "now"
    assert rates.update("wg/1", None, 100, now=1010.0) is None


def test_openwrt_helpers_tell_when_they_are_older() -> None:
    from kastellan.openwrt import setup

    current = "\n".join(f"{k}={v}" for k, v in setup.helper_digests().items())
    assert setup.outdated(current) == []
    assert setup.outdated(None) == list(setup.helper_digests())
    assert setup.outdated(current.replace("openvpn-status=", "openvpn-status=x")) == ["openvpn-status"]
    script = setup.install_script({"openvpn"}, setup.md5crypt("x"), [])
    assert setup.VERSIONS in script and f"acl={setup.helper_digests()['acl']}" in script


async def test_vpn_helpers_tell_when_they_are_older(monkeypatch) -> None:
    from types import SimpleNamespace

    from kastellan.vpn import openvpn

    me = openvpn.OpenVpn.__new__(openvpn.OpenVpn)
    me.s = SimpleNamespace(mode="restricted", pki_user="k", server_user="root", crl_path="/etc/crl.pem")
    me.pki_device, me.server_device = object(), object()
    monkeypatch.setattr(openvpn, "pki_conf", lambda s: "")
    monkeypatch.setattr(openvpn, "crl_conf", lambda s: "")
    answers = {
        "kastellan-pki": openvpn.helper_digest("kastellan-pki") + "\n",
        "kastellan-crl": RuntimeError("kastellan-crl: only 'install' (and add-key / keep-key)"),
    }

    async def run(device, user, name, conf, command, input=None):
        assert command == "version"
        a = answers[name]
        if isinstance(a, Exception):
            raise a
        return a

    me._run = run
    assert await me.outdated_helpers() == ["crl"]
    answers["kastellan-crl"] = OSError("unreachable")
    assert await me.outdated_helpers() == []
    assert await me.outdated_helpers(["crl"]) == ["crl"]
    me.s.mode = "direct"
    assert await me.outdated_helpers() == []


def test_vpn_helpers_say_their_own_digest() -> None:
    import shutil
    import subprocess

    from kastellan.vpn import openvpn

    if shutil.which("sha256sum") and shutil.which("bash"):  # the scripts say their own digest
        for name, shell in (("kastellan-crl", "sh"), ("kastellan-pki", "bash")):
            path = openvpn.SCRIPTS / name
            out = subprocess.run(
                [shell, str(path), "/nonexistent.conf"],
                env={"SSH_ORIGINAL_COMMAND": "version", "PATH": "/usr/bin:/bin:/sbin"},
                capture_output=True,
                text=True,
            )
            assert out.stdout.strip() == openvpn.helper_digest(name), out.stderr
