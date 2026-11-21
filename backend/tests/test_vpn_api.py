"""VPN management end to end: the app, a fake wg-easy (15.5's routes), and SSH servers that run
stand-ins for kastellan-pki and kastellan-crl (the real scripts are tested against easy-rsa by
hand; see tools/)."""

import asyncio
import base64
import concurrent.futures
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient

from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings
from kastellan.ssh import Keys

H = {"X-Kastellan": "1"}
HASH = hash_password("pw for tests")


@pytest.fixture
def wg_easy():
    """A wg-easy that knows one client and checks the admin login."""
    state = {"clients": [{"id": 1, "name": "phone", "enabled": True, "ipv4Address": "10.8.0.2"}], "next": 2}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:
            pass

        def _auth(self) -> bool:
            expected = "Basic " + base64.b64encode(b"admin:wg-secret").decode()
            if self.headers.get("Authorization") != expected:
                self.send_response(401)
                self.end_headers()
                return False
            return True

        def _send(self, code: int, body: object, kind: str = "application/json") -> None:
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if not self._auth():
                return
            if self.path == "/api/client":
                return self._send(200, state["clients"])
            if self.path.endswith("/configuration"):
                return self._send(200, "[Interface]\nPrivateKey = secret\n", "application/octet-stream")
            if self.path.endswith("/qrcode.svg"):
                return self._send(200, "<svg></svg>", "image/svg+xml")
            self._send(404, {})

        def do_POST(self) -> None:
            if not self._auth():
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/client":
                state["clients"].append({"id": state["next"], "name": body["name"], "enabled": True})
                state["next"] += 1
                return self._send(200, {"success": True, "clientId": state["next"] - 1})
            id, action = self.path.split("/")[-2:]
            for c in state["clients"]:
                if str(c["id"]) == id:
                    c["enabled"] = action == "enable"
            self._send(200, {"success": True})

        def do_DELETE(self) -> None:
            if not self._auth():
                return
            id = self.path.rsplit("/", 1)[-1]
            state["clients"] = [c for c in state["clients"] if str(c["id"]) != id]
            self._send(200, {"success": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()


@pytest.fixture
def pki(tmp_path: Path):
    """Two SSH servers on one thread: the PKI host (kastellan-pki) and the OpenVPN server
    (kastellan-crl), each running only its command, like command= in authorized_keys."""
    keys = Keys(tmp_path / "data" / "ssh")
    certs: dict[str, str] = {}
    installed: list[str] = []
    broken: list[bool] = []  # non-empty: the OpenVPN server refuses CRLs
    loop = asyncio.new_event_loop()
    started: concurrent.futures.Future = concurrent.futures.Future()
    host_key = asyncssh.generate_private_key("ssh-ed25519")

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username: str) -> bool:
            return True

    async def kastellan_pki(process: asyncssh.SSHServerProcess) -> None:
        words = (process.command or "").split()
        command, name = [*words, "", ""][:2]
        if command == "list":
            for n, status in certs.items():
                process.stdout.write(f"{status}\t271231235959Z\t01\t{n}\n")
        elif command == "issue" and name not in certs:
            certs[name] = "V"
        elif command == "revoke" and name in certs:
            certs[name] = "R"
        elif command == "renew-crl":
            pass
        elif command == "crl":
            process.stdout.write(
                "-----BEGIN X509 CRL-----\nrevoked: "
                + ",".join(n for n, s in certs.items() if s == "R")
                + "\n-----END X509 CRL-----\n"
            )
        elif command == "export" and certs.get(name) == "V":
            process.stdout.write(f"client\n# {name}\n<key>\nsecret\n</key>\n")
        else:
            process.stderr.write("kastellan-pki: refused\n")
            process.exit(2)
            return
        process.exit(0)

    async def kastellan_crl(process: asyncssh.SSHServerProcess) -> None:
        if broken:
            process.stderr.write("kastellan-crl: disk full\n")
            process.exit(2)
            return
        installed.append(await process.stdin.read())
        process.exit(0)

    async def serve() -> None:
        # The terminal key and the VPN key (restricted mode logs in with the latter).
        authorized = asyncssh.import_authorized_keys(keys.public_key() + "\n" + keys.public_key("vpn"))
        a = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[host_key],
            authorized_client_keys=authorized,
            process_factory=kastellan_pki,
        )
        b = await asyncssh.create_server(
            Server,
            "127.0.0.1",
            0,
            server_host_keys=[host_key],
            authorized_client_keys=authorized,
            process_factory=kastellan_crl,
        )
        started.set_result((a, b))

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    asyncio.run_coroutine_threadsafe(serve(), loop)
    a, b = started.result(timeout=10)
    yield a.sockets[0].getsockname()[1], b.sockets[0].getsockname()[1], host_key, certs, installed, broken
    loop.call_soon_threadsafe(a.close)
    loop.call_soon_threadsafe(b.close)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)


def make_client(tmp_path: Path, wg_url: str, pki_port: int, crl_port: int) -> TestClient:
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
devices:
  sv1:
    host: 127.0.0.1
    ssh: {{user: kastellan, port: {pki_port}}}
  openwrt:
    host: 127.0.0.1
    ssh: {{user: root, port: {crl_port}}}
vpn:
  wireguard:
    wg_easy: {wg_url}
  openvpn:
    pki: sv1
    server: openwrt
    days: 90
users:
  ada:
    role: admin
    password: {HASH}
  obi:
    role: observer
    password: {HASH}
"""
    )
    return TestClient(create_app(Settings(config=config, data=tmp_path / "data")))


def signin(c: TestClient, user: str = "ada") -> None:
    assert (
        c.post("/api/auth/login", json={"user": user, "password": "pw for tests"}, headers=H).status_code
        == 200
    )


def test_wireguard(tmp_path: Path, wg_easy, pki) -> None:
    url, state = wg_easy
    with make_client(tmp_path, url, pki[0], pki[1]) as c:
        signin(c)
        # Without the secret: said plainly.
        assert "kastellan secret set wg-easy" in c.get("/api/vpn").json()["wireguard"]["error"]
        c.app.state.hub.secrets.set("wg-easy", "wrong")
        assert c.get("/api/vpn").json()["wireguard"]["error"] == "wg-easy refused the login"
        c.app.state.hub.secrets.set("wg-easy", "wg-secret")
        assert [x["name"] for x in c.get("/api/vpn").json()["wireguard"]["clients"]] == ["phone"]

        assert c.post("/api/vpn/wireguard", json={"name": "laptop", "days": 30}, headers=H).json() == {
            "id": "2"
        }
        assert c.post("/api/vpn/wireguard/2/disable", headers=H).status_code == 200
        assert state["clients"][1]["enabled"] is False
        r = c.get("/api/vpn/wireguard/2/config")
        assert r.status_code == 200 and "PrivateKey" in r.text
        assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
        assert c.delete("/api/vpn/wireguard/2", headers=H).status_code == 200
        assert c.post("/api/vpn/wireguard/1/reboot", headers=H).status_code == 404

        log = (tmp_path / "data" / "audit.log").read_text()
        assert all(
            a in log for a in ("vpn.wireguard.create", "vpn.wireguard.download", "vpn.wireguard.delete")
        )
        assert "wg-secret" not in log


def test_openvpn(tmp_path: Path, wg_easy, pki) -> None:
    pki_port, crl_port, host_key, certs, installed, broken = pki
    with make_client(tmp_path, wg_easy[0], pki_port, crl_port) as c:
        signin(c)
        hub = c.app.state.hub
        # The hosts' keys must be trusted first, like for the terminal.
        assert "not trusted" in c.get("/api/vpn").json()["openvpn"]["error"]
        for device in ("sv1", "openwrt"):
            r = c.post(
                f"/api/devices/{device}/ssh/trust",
                json={"fingerprint": host_key.get_fingerprint()},
                headers=H,
            )
            assert r.status_code == 200
        assert c.post("/api/vpn/openvpn", json={"name": "alice"}, headers=H).status_code == 200
        assert certs == {"alice": "V"}
        assert c.post("/api/vpn/openvpn", json={"name": "../evil"}, headers=H).status_code == 400
        assert c.get("/api/vpn").json()["openvpn"]["clients"][0]["status"] == "valid"

        from datetime import UTC, datetime

        # Who the router said is connected counts only while it said so lately, and not on errors.
        alice = {"name": "alice", "address": "203.0.113.5:1", "since": None, "rx": 1, "tx": 2}
        now = datetime.now(UTC).isoformat(timespec="seconds")

        def connected() -> object:
            clients = c.get("/api/vpn").json()["openvpn"]["clients"]
            return next(x for x in clients if x["name"] == "alice")["connected"]

        hub.ovpn_status = {"checked": now, "connected": [alice], "error": None}
        assert connected() == alice
        hub.ovpn_status = {"checked": "2026-11-20T00:00:00+00:00", "connected": [alice], "error": None}
        assert connected() is None  # stale: the router hasn't been read for a while
        hub.ovpn_status = {"checked": now, "connected": [alice], "error": "it doesn't answer"}
        assert connected() is None

        r = c.get("/api/vpn/openvpn/alice/config")
        assert (
            r.status_code == 200
            and "<key>" in r.text
            and 'filename="alice.ovpn"' in r.headers["content-disposition"]
        )

        assert c.post("/api/vpn/openvpn/alice/revoke", headers=H).json() == {
            "revoked": True,
            "crl_pushed": True,
        }
        assert certs == {"alice": "R"} and "revoked: alice" in installed[-1]  # the server has the new CRL
        assert c.get("/api/vpn/openvpn/alice/config").status_code == 400  # revoked: no more config
        assert c.post("/api/vpn/openvpn", json={"name": "server"}, headers=H).status_code == 400  # reserved
        assert c.post("/api/vpn/openvpn/server/revoke", headers=H).status_code == 400

        # The server can't take the CRL: the revoke still stands and is audited; push later.
        c.post("/api/vpn/openvpn", json={"name": "bob"}, headers=H)
        broken.append(True)
        r = c.post("/api/vpn/openvpn/bob/revoke", headers=H).json()
        assert r["revoked"] and not r["crl_pushed"] and "disk full" in r["error"]
        assert c.get("/api/vpn").json()["openvpn"]["crl"]["error"]
        broken.clear()
        assert c.post("/api/vpn/openvpn/crl", headers=H).status_code == 200
        assert "revoked: alice,bob" in installed[-1]
        log = (tmp_path / "data" / "audit.log").read_text()
        assert '"vpn.openvpn.revoke"' in log and "failed: " in log
        del hub


def test_observers_see_but_dont_touch(tmp_path: Path, wg_easy, pki) -> None:
    with make_client(tmp_path, wg_easy[0], pki[0], pki[1]) as c:
        signin(c, "obi")
        seen = c.get("/api/vpn").json()
        assert seen["openvpn"]["error"] == "The PKI host can't be reached right now"  # no details
        assert c.post("/api/vpn/wireguard", json={"name": "x"}, headers=H).status_code == 403
        assert c.get("/api/vpn/wireguard/1/config").status_code == 403  # a private key: admins only
        assert c.get("/api/vpn/openvpn/alice/config").status_code == 403


def test_setup_page(tmp_path: Path, wg_easy, pki) -> None:
    import subprocess

    pki_port, crl_port, host_key, *_ = pki
    with make_client(tmp_path, wg_easy[0], pki_port, crl_port) as c:
        signin(c, "obi")
        assert c.get("/api/vpn/openvpn/setup").status_code == 403  # admins only
        signin(c)
        setup = c.get("/api/vpn/openvpn/setup").json()
        assert setup["mode"] == "restricted" and setup["vpn_key"].startswith("ssh-ed25519 ")
        pki_host, server = setup["hosts"]
        assert pki_host["checks"][0] == {
            "name": "Host key trusted",
            "ok": False,
            "detail": f"trust {host_key.get_fingerprint()} on the device's terminal page",
        }
        # The scripts to copy: valid shell, with the VPN key bound to the scripts.
        for host in (pki_host, server):
            assert subprocess.run(["sh", "-n"], input=host["script"], text=True).returncode == 0
            assert setup["vpn_key"] in host["script"]
        assert 'restrict,command="/usr/local/bin/kastellan-pki /etc/kastellan-pki.conf"' in pki_host["script"]
        assert 'command="/usr/bin/kastellan-crl /etc/kastellan-crl.conf"' in server["script"]

        for device in ("sv1", "openwrt"):
            c.post(
                f"/api/devices/{device}/ssh/trust",
                json={"fingerprint": host_key.get_fingerprint()},
                headers=H,
            )
        pki_host = c.get("/api/vpn/openvpn/setup").json()["hosts"][0]
        assert [ch["ok"] for ch in pki_host["checks"][:2]] == [True, True]  # trusted, the script answers


def test_last_seen_and_the_key(tmp_path: Path, wg_easy, pki) -> None:
    from datetime import UTC, datetime

    from kastellan.vpn.openvpn import Connection

    with make_client(tmp_path, wg_easy[0], pki[0], pki[1]) as c:
        signin(c)
        hub = c.app.state.hub
        vpn = c.get("/api/vpn").json()["openvpn"]
        assert vpn["seen"]["how"] == "no-openwrt"  # the server has no openwrt: login
        hub.ovpn_seen.note([Connection("alice", None, None, None, None)], datetime(2026, 11, 21, tzinfo=UTC))
        assert hub.ovpn_seen.last == {"alice": "2026-11-21T00:00:00+00:00"}

        key = c.get("/api/vpn/key").json()
        assert key["fingerprint"] == hub.keys.hub_key("vpn").get_fingerprint() and key["rotation"] is None
        # Rotating needs sign-in and the CSRF header like every change; the hosts' keys aren't
        # trusted here, so adding fails per host and is reported, not raised.
        assert c.post("/api/vpn/key/rotate/start").status_code == 403
        r = c.post("/api/vpn/key/rotate/start", headers=H).json()
        assert set(r["rotation"]["devices"]) == {"pki", "server"}
        assert not any(s["added"] for s in r["rotation"]["devices"].values())
        assert c.post("/api/vpn/key/rotate/finish", json={}, headers=H).status_code == 409
        assert c.post("/api/vpn/key/rotate/forget", json={"host": "pki"}, headers=H).status_code == 409
        assert c.post("/api/vpn/key/rotate/cancel", headers=H).json()["rotation"] is None
        log = (tmp_path / "data" / "audit.log").read_text()
        assert '"vpn.key.rotate.start"' in log and '"vpn.key.rotate.cancel"' in log
        cancel = json.loads(next(ln for ln in log.splitlines() if '"vpn.key.rotate.cancel"' in ln))
        assert cancel["stage"] == "cancelled" and set(cancel["hosts"]) == {"pki", "server"}
        signin(c, "obi")
        assert c.get("/api/vpn/key").status_code == 403
