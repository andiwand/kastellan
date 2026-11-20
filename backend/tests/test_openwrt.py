"""OpenWrt: the login's setup, and the client against a fake rpcd that enforces Kastellan's own
access list (so every call the client makes must be one its abilities grant)."""

import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from kastellan.openwrt import client as ow
from kastellan.openwrt import firmware as fw
from kastellan.openwrt.setup import acl, install_script, md5crypt

VERSIONS = {
    "stable_version": "25.12.6",
    "oldstable_version": "24.10.8",
    "versions_list": [
        "25.12.6",
        "25.12.0",
        "24.10.8",
        "24.10.0",
        "23.05.6",
        "23.05.2",
        "22.03.7",
        "22.03.5",
        "24.10.0-rc1",
    ],
}


def test_md5crypt_matches_crypt() -> None:
    assert md5crypt("password", "saltsalt") == "$1$saltsalt$qjXMvbEw8oaL.CzflDtaK/"
    assert md5crypt("hello world", "abcdefgh") == "$1$abcdefgh$gJuEE5kgk4GxZUtJo4ZFv1"
    assert md5crypt("x").startswith("$1$") and md5crypt("x") != md5crypt("x")  # a new salt each time


def test_the_install_script() -> None:
    script = install_script({"reboot"}, md5crypt("pw", "abcdefgh"))
    assert subprocess.run(["sh", "-n"], input=script, text=True).returncode == 0
    body = script.split("<<'KASTELLAN_ACL_EOF'\n", 1)[1].split("\nKASTELLAN_ACL_EOF", 1)[0]
    assert json.loads(body) == acl()
    assert 'uci add_list "rpcd.$s.write=kastellan-reboot"' in script
    assert "kastellan-wifi" not in script.split("KASTELLAN_ACL_EOF")[-1]  # not chosen: not granted
    assert "'$1$abcdefgh$" in script and "pw" not in script.replace("rpcd", "")  # only the hash
    with pytest.raises(ValueError):
        install_script({"shell"}, md5crypt("pw"))


@pytest.mark.parametrize(
    ("current", "supported", "next_step"),
    [
        ("23.05.2", False, "23.05.6"),
        ("23.05.6", False, "24.10.8"),
        ("22.03.5", False, "22.03.7"),
        ("24.10.8", True, "25.12.6"),
        ("25.12.6", True, None),
    ],
)
def test_firmware_advice(current: str, supported: bool, next_step: str | None) -> None:
    a = fw.advise(current, VERSIONS)
    assert a is not None and (a.supported, a.next_step) == (supported, next_step)
    assert fw.advise("SNAPSHOT", VERSIONS) is None


class FakeRpcd:
    """rpcd as far as Kastellan uses it: a login with groups, each call checked against acl()."""

    def __init__(self, groups: list[str]) -> None:
        self.groups = ["kastellan-read", *groups]
        self.calls: list[tuple[str, str, dict]] = []
        self.wireless = {
            "radio0": {".type": "wifi-device", "channel": "36", "band": "5g", "disabled": "0"},
            "default_radio0": {
                ".type": "wifi-iface",
                "device": "radio0",
                "ssid": "home",
                "key": "secret1",
                "encryption": "sae",
                "mode": "ap",
                "network": "lan",
            },
        }
        self.staged: dict = {}
        self.firmware = b""
        self.session = "s1"
        self.logins = 0
        self.radio_up = True  # after applying
        self.applied = False
        self.rolled_back = False

    def file_allowed(self, path: str, function: str) -> bool:
        """rpcd's `file` scope: a path and what may be done with it."""
        return any(
            function in acl()[g].get(kind, {}).get("file", {}).get(path, [])
            for g in self.groups
            for kind in ("read", "write")
        )

    def allowed(self, obj: str, method: str, args: dict) -> bool:
        ubus_ok = any(
            method in acl()[g].get(kind, {}).get("ubus", {}).get(obj, [])
            for g in self.groups
            for kind in ("read", "write")
        )
        if not ubus_ok:
            return False
        if obj == "file":  # the file plugin checks the path too
            path = args.get("command") or args.get("path")
            return self.file_allowed(path, {"remove": "write"}.get(method, method))
        if obj == "uci":
            return args.get("config") in (None, "wireless")
        return True

    def upload(self, request: httpx.Request) -> httpx.Response:
        boundary = request.headers["content-type"].split("boundary=")[1].encode()
        fields = {}
        for part in request.content.split(b"--" + boundary)[1:-1]:
            head, _, value = part.partition(b"\r\n\r\n")
            name = head.split(b'name="')[1].split(b'"')[0].decode()
            fields[name] = value[:-2]
        path = fields["filename"].decode()
        if fields["sessionid"].decode() != self.session or not self.file_allowed(path, "write"):
            return httpx.Response(403)
        self.firmware = fields["filedata"]
        return httpx.Response(
            200, json={"size": len(self.firmware), "sha256sum": hashlib.sha256(self.firmware).hexdigest()}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/cgi-upload":
            return self.upload(request)
        body = json.loads(request.content)
        session, obj, method, args = body["params"]
        self.calls.append((obj, method, args))
        if (obj, method) == ("session", "login"):
            ok = args == {"username": "kastellan", "password": "pw"}
            self.logins += ok
            return httpx.Response(
                200, json={"result": [0, {"ubus_rpc_session": self.session, "expires": 300}] if ok else [6]}
            )
        if session != self.session:
            return httpx.Response(200, json={"error": {"code": -32002, "message": "Access denied"}})
        if (obj, method) == ("session", "access") and self.allowed(obj, method, args):
            return httpx.Response(
                200, json={"result": [0, {"access-group": {g: ["read"] for g in self.groups}}]}
            )
        if not self.allowed(obj, method, args):  # uhttpd checks the ACL before calling
            return httpx.Response(200, json={"error": {"code": -32002, "message": "Access denied"}})
        answers = {
            ("system", "board"): {
                "model": "Xiaomi AX3000T",
                "hostname": "wifi1",
                "board_name": "xiaomi,mi-router-ax3000t",
                "release": {"version": "23.05.2", "revision": "r23630", "target": "mediatek/filogic"},
            },
            ("system", "info"): {
                "uptime": 100,
                "load": [65536, 32768, 0],
                "memory": {"total": 2**28, "free": 2**27},
            },
            ("network.interface", "dump"): {
                "interface": [{"interface": "lan", "up": True, "ipv4-address": [{"address": "192.168.15.1"}]}]
            },
            ("network.wireless", "status"): {
                "radio0": {
                    "up": True,
                    "config": {"band": "5g", "channel": "36"},
                    "interfaces": [
                        {
                            "section": "default_radio0",
                            "ifname": "phy0-ap0",
                            "config": {"ssid": "home", "mode": "ap"},
                        }
                    ],
                }
            },
            ("iwinfo", "assoclist"): {"results": [{"mac": "AA:BB:CC:00:11:22", "signal": -55}]},
            ("luci-rpc", "getDHCPLeases"): {
                "dhcp_leases": [{"macaddr": "aa:bb:cc:00:11:22", "hostname": "phone"}]
            },
            ("file", "exec"): {"stdout": "curl - 8.1 - 8.2\n"},
            ("system", "reboot"): {},
            ("rc", "list"): {"dropbear": {"enabled": True, "running": True}, "openvpn": {"enabled": False}},
            ("file", "read"): {"data": "dropbear\n"},
            ("uci", "get"): {"values": self.wireless},
            ("rpc-sys", "packagelist"): {"packages": {"luci": "1", "wpad-basic-wolfssl": "1"}},
            ("rpc-sys", "upgrade_test"): {"code": 0},
            ("rpc-sys", "upgrade_start"): {},
        }
        if (obj, method) == ("file", "exec") and args["command"].endswith("/service"):
            return httpx.Response(200, json={"result": [0, {"code": 0, "stdout": " ".join(args["params"])}]})
        if (obj, method) == ("uci", "set"):
            self.staged.setdefault(args["section"], {}).update(args["values"])
        if (obj, method) == ("uci", "apply"):
            self.applied = True
        if (obj, method) == ("uci", "rollback"):
            self.rolled_back, self.staged = True, {}
        if (obj, method) == ("network.wireless", "status") and self.applied and not self.radio_up:
            return httpx.Response(200, json={"result": [0, {"radio0": {"up": False}}]})
        if (obj, method) == ("uci", "confirm"):
            for sec, vals in self.staged.items():
                self.wireless[sec].update(vals)
        return httpx.Response(200, json={"result": [0, answers.get((obj, method), {})]})


def ubus(tmp_path: Path, rpcd: FakeRpcd, password: str = "pw") -> ow.Ubus:
    return ow.Ubus(
        "wifi1",
        "192.168.15.1",
        443,
        "kastellan",
        password,
        ow.Pins(tmp_path),
        transport=httpx.MockTransport(rpcd.handle),
    )


async def test_reading_needs_only_the_read_group(tmp_path: Path) -> None:
    rpcd = FakeRpcd([])
    report = await ow.read(ubus(tmp_path, rpcd))
    assert report["board"]["release"]["version"] == "23.05.2" and report["errors"] == {}
    assert report["system"]["load"] == [1.0, 0.5, 0.0]
    assert report["services"][0] == {"name": "dropbear", "enabled": True, "running": True}
    assert report["services_allowed"] == ["dropbear"]
    with pytest.raises(ow.UbusError):  # acting on services wasn't ticked
        await ow.service(ubus(tmp_path, rpcd), "dropbear", "restart")
    assert (
        await ow.service(ubus(tmp_path, FakeRpcd(["kastellan-services"])), "dropbear", "restart")
        == "dropbear restart"
    )
    (radio,) = report["radios"]
    assert radio["interfaces"][0]["clients"] == [
        {"mac": "aa:bb:cc:00:11:22", "name": "phone", "signal": -55, "inactive": None}
    ]
    assert await ow.upgradable(ubus(tmp_path, rpcd)) == ["curl - 8.1 - 8.2"]
    # Without the reboot group, the device refuses.
    with pytest.raises(ow.UbusError) as e:
        await ow.reboot(ubus(tmp_path, rpcd))
    assert e.value.code == ow.DENIED
    with pytest.raises(ow.UbusError):
        await ow.wifi_settings(ubus(tmp_path, rpcd))


async def test_a_wrong_password_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ow.LoginRefused):
        await ow.read(ubus(tmp_path, FakeRpcd([]), password="nope"))


async def test_wifi_with_rollback_and_keys_hidden(tmp_path: Path) -> None:
    rpcd = FakeRpcd(["kastellan-wifi"])
    u = ubus(tmp_path, rpcd)
    settings = await ow.wifi_settings(u)
    iface = settings["interfaces"][0]
    assert iface["ssid"] == "home" and iface["key_set"] and "key" not in iface
    await ow.change_wifi(
        u,
        {"default_radio0": {"ssid": "home2", "key": "secret22", "network": "wan"}, "radio0": {"channel": 44}},
        confirm_after=0,
    )
    assert (
        rpcd.wireless["default_radio0"]["ssid"] == "home2"
        and rpcd.wireless["default_radio0"]["key"] == "secret22"
    )
    assert rpcd.wireless["default_radio0"]["network"] == "lan"  # not a field the page may change
    assert rpcd.wireless["radio0"]["channel"] == "44"
    kinds = [(o, m) for o, m, _ in rpcd.calls if o == "uci"]
    assert ("uci", "apply") in kinds and kinds[-1] == ("uci", "confirm")
    assert next(a for o, m, a in rpcd.calls if m == "apply") == {"rollback": True, "timeout": 90}


async def test_installing_firmware(tmp_path: Path) -> None:
    rpcd = FakeRpcd(["kastellan-firmware"])
    image = b"\x00" * 300_000
    sha = hashlib.sha256(image).hexdigest()
    with pytest.raises(ValueError, match="checksum"):
        await ow.install_firmware(ubus(tmp_path, rpcd), image, "0" * 64)
    assert await ow.install_firmware(ubus(tmp_path, rpcd), image, sha) == {"started": True}
    assert rpcd.firmware == image
    assert [m for o, m, _ in rpcd.calls if o == "rpc-sys"][-2:] == ["upgrade_test", "upgrade_start"]
    assert next(a for o, m, a in rpcd.calls if m == "upgrade_start") == {"keep": True}


async def test_an_image_from_the_builder() -> None:
    image = b"firmware-bytes"
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["profile"] == "xiaomi_mi-router-ax3000t" and body["version"] == "24.10.8"
            return httpx.Response(202, json={"request_hash": "abc", "imagebuilder_status": "building"})
        if request.url.path == "/api/v1/build/abc":
            return httpx.Response(
                200,
                json={
                    "bin_dir": "x/y",
                    "images": [
                        {"type": "factory", "name": "f.bin", "sha256": "0"},
                        {"type": "sysupgrade", "name": "s.bin", "sha256": hashlib.sha256(image).hexdigest()},
                    ],
                },
            )
        return httpx.Response(200, content=image)

    import kastellan.openwrt.firmware as module

    real_sleep = module.asyncio.sleep
    module.asyncio.sleep = lambda s: real_sleep(0)  # type: ignore[assignment]
    try:
        got, _, name = await fw.build_image(
            "mediatek/filogic",
            "xiaomi,mi-router-ax3000t",
            "24.10.8",
            ["luci"],
            transport=httpx.MockTransport(handle),
        )
    finally:
        module.asyncio.sleep = real_sleep
    assert got == image and name == "s.bin" and "GET /store/x/y/s.bin" in asked


def test_the_service_script(tmp_path: Path) -> None:
    """The one script the services ability may run: only allowed services, only five actions,
    never stopping Kastellan's own way in."""
    from kastellan.openwrt import setup

    init = tmp_path / "init.d"
    init.mkdir()
    for name in ("dropbear", "firewall", "rpcd"):
        (init / name).write_text(f'#!/bin/sh\necho "{name} $1"\n')
        (init / name).chmod(0o755)
    allow = tmp_path / "allow"
    allow.write_text("dropbear\nrpcd\n")
    script = setup._SERVICE_SCRIPT.replace(setup.SERVICES_ALLOW, str(allow)).replace("/etc/init.d", str(init))
    script = script.replace("logger -t kastellan", ": ")
    path = tmp_path / "service"
    path.write_text(script)
    path.chmod(0o755)

    def run(*args: str) -> tuple[int, str]:
        r = subprocess.run([str(path), *args], capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    assert run("dropbear", "restart") == (0, "dropbear restart")
    assert run("rpcd", "restart") == (0, "rpcd restart")
    assert run("rpcd", "stop")[0] == 3
    assert run("firewall", "stop")[0] == 3  # not in the allow list
    assert run("dropbear", "boot")[0] == 2
    assert run("../dropbear", "stop")[0] == 2
    assert run("drop", "stop")[0] == 3  # whole lines only
    assert run("dropbear\nfirewall", "stop")[0] == 2
    script_text = install_script({"services"}, md5crypt("pw", "abcdefgh"), ["dropbear", "openvpn"])
    assert "printf '%s\\n' dropbear openvpn > /etc/kastellan/services.allow.new" in script_text
    assert ": > /etc/kastellan/services.allow.new" in install_script({"reboot"}, md5crypt("pw"), ["dropbear"])
    with pytest.raises(ValueError):
        install_script({"services"}, md5crypt("pw"), ["x; reboot"])


async def test_an_ended_session_logs_in_again_once(tmp_path: Path) -> None:
    rpcd = FakeRpcd([])
    u = ubus(tmp_path, rpcd)
    await ow.read(u)
    rpcd.session = "s2"  # the device rebooted: the old session is gone
    report = await ow.read(u)
    assert report["board"]["model"] and rpcd.logins == 2


async def test_wifi_values_are_checked(tmp_path: Path) -> None:
    rpcd = FakeRpcd(["kastellan-wifi"])
    u = ubus(tmp_path, rpcd)
    for bad in (
        {"default_radio0": {"key": "secret22\nctrl_interface=/tmp"}},  # an extra hostapd line
        {"default_radio0": {"ssid": "x" * 33}},
        {"default_radio0": {"encryption": "wep; reboot"}},
        {"radio0": {"channel": "36 ; x"}},
        {"nosuch": {"ssid": "a"}},
    ):
        with pytest.raises(ValueError):
            await ow.change_wifi(u, bad, confirm_after=0)
    assert not any(m == "set" for _, m, _ in rpcd.calls)  # nothing was staged
    await ow.change_wifi(u, {"default_radio0": {"disabled": True}}, confirm_after=0)
    assert rpcd.wireless["default_radio0"]["disabled"] == "1"  # not "True"


async def test_a_radio_that_doesnt_come_up_is_rolled_back(tmp_path: Path) -> None:
    rpcd = FakeRpcd(["kastellan-wifi"])
    rpcd.radio_up = False
    with pytest.raises(ValueError, match="undone"):
        await ow.change_wifi(ubus(tmp_path, rpcd), {"radio0": {"channel": "165"}}, confirm_after=0)
    assert rpcd.rolled_back and rpcd.wireless["radio0"]["channel"] == "36"
    assert not any(m == "confirm" for _, m, _ in rpcd.calls)


async def test_a_damaged_upload_is_removed(tmp_path: Path) -> None:
    rpcd = FakeRpcd(["kastellan-firmware"])
    image = b"\x01" * 1000
    real = rpcd.upload

    def damaging(request: httpx.Request) -> httpx.Response:
        real(request)
        rpcd.firmware = rpcd.firmware[:-1]
        return httpx.Response(200, json={"size": len(rpcd.firmware), "sha256sum": "0" * 64})

    rpcd.upload = damaging  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="damaged"):
        await ow.install_firmware(ubus(tmp_path, rpcd), image, hashlib.sha256(image).hexdigest())
    assert ("file", "remove", {"path": "/tmp/firmware.bin"}) in rpcd.calls
    assert not any(m == "upgrade_start" for _, m, _ in rpcd.calls)


def test_the_certificate_from_the_setup() -> None:
    assert ow.fingerprint_from_setup(["x", "KASTELLAN-CERT der " + "AB" * 32]) == "ab" * 32
    assert ow.fingerprint_from_setup(["KASTELLAN-CERT der nothex"]) is None
    assert ow.fingerprint_from_setup(["nothing"]) is None
    script = install_script({"firmware"}, md5crypt("pw"))
    assert "KASTELLAN-CERT der" in script and "rpcd-mod-rpcsys" in script and "cgi-upload" in script
    assert "rpcd-mod-rpcsys" not in install_script({"wifi"}, md5crypt("pw"))


async def test_pinning_checks_the_certificate_itself(tmp_path: Path) -> None:
    """A real TLS server: the pinned fingerprint passes even if the certificate has expired; any
    other certificate gets nothing (not even the request)."""
    import asyncio
    import datetime as dt
    import ssl

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OpenWrt")])
    old = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(old)
        .not_valid_after(old + dt.timedelta(days=397))
        .sign(key, hashes.SHA256())
    )
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    got: list[bytes] = []

    async def serve(reader, writer) -> None:
        got.append(await reader.read(65536))
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": [0, {"ubus_rpc_session": "s", "expires": 300}]}
        )
        writer.write(f"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0, ssl=ctx)
    port = server.sockets[0].getsockname()[1]
    pins = ow.Pins(tmp_path / "pins")
    u = ow.Ubus("r", "127.0.0.1", port, "kastellan", "pw", pins)
    async with server:
        with pytest.raises(ow.ChangedCertificate):
            await u.session_id()  # nothing pinned
        pins.set("r", "0" * 64)
        with pytest.raises(ow.ChangedCertificate):
            await u.session_id()
        assert not any(got)  # the password was never sent
        pins.set("r", hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest())
        assert await u.session_id() == "s"  # expired, but the very certificate
