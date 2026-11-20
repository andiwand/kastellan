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
        installed.append(await process.stdin.read())
        process.exit(0)

    async def serve() -> None:
        authorized = asyncssh.import_authorized_keys(keys.public_key())
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
    yield a.sockets[0].getsockname()[1], b.sockets[0].getsockname()[1], host_key, certs, installed
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
    pki_port, crl_port, host_key, certs, installed = pki
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
        del hub


def test_observers_see_but_dont_touch(tmp_path: Path, wg_easy, pki) -> None:
    with make_client(tmp_path, wg_easy[0], pki[0], pki[1]) as c:
        signin(c, "obi")
        assert c.get("/api/vpn").status_code == 200
        assert c.post("/api/vpn/wireguard", json={"name": "x"}, headers=H).status_code == 403
        assert c.get("/api/vpn/wireguard/1/config").status_code == 403  # a private key: admins only
        assert c.get("/api/vpn/openvpn/alice/config").status_code == 403
