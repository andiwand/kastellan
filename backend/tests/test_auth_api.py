from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.settings import Settings

H = {"X-Kastellan": "1"}
ADMIN_HASH = hash_password("admin password")
OBSERVER_HASH = hash_password("observer password")


def write_config(path: Path, users: bool = True) -> None:
    text = "devices:\n  box:\n    host: 127.0.0.1\n"
    if users:
        text += (
            "users:\n"
            f"  ada:\n    role: admin\n    password: {ADMIN_HASH}\n"
            f"  obi:\n    role: observer\n    password: {OBSERVER_HASH}\n"
        )
    path.write_text(text)


@pytest.fixture
def client(tmp_path: Path):
    config = tmp_path / "config.yaml"
    write_config(config)
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        yield c


def signin(client: TestClient, user: str, password: str) -> int:
    return client.post("/api/auth/login", json={"user": user, "password": password}, headers=H).status_code


def test_signed_out_sees_nothing(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/devices").status_code == 401
    assert client.get("/api/auth/me").json() == {"signin": True, "user": None, "role": None}


def test_wrong_password_and_unknown_user_look_the_same(client: TestClient) -> None:
    a = client.post("/api/auth/login", json={"user": "ada", "password": "nope"}, headers=H)
    b = client.post("/api/auth/login", json={"user": "nobody", "password": "nope"}, headers=H)
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_admin_and_observer(client: TestClient, tmp_path: Path) -> None:
    assert signin(client, "obi", "observer password") == 200
    assert client.get("/api/devices").status_code == 200
    assert client.get("/api/auth/me").json()["role"] == "observer"
    assert client.get("/api/audit").status_code == 403  # admins only
    assert client.post("/api/anything", headers=H).status_code == 403  # observers don't write
    assert client.post("/api/auth/logout", headers=H).status_code == 200
    assert client.get("/api/devices").status_code == 401

    assert signin(client, "ada", "admin password") == 200
    actions = [e["action"] for e in client.get("/api/audit").json()]
    assert actions[:3] == ["signin", "signout", "signin"]


def test_writes_need_the_header(client: TestClient) -> None:
    signin(client, "ada", "admin password")
    r = client.post("/api/auth/logout")  # no X-Kastellan: a forged cross-site request
    assert r.status_code == 403
    assert client.get("/api/devices").status_code == 200  # still signed in


def test_brake(client: TestClient) -> None:
    for _ in range(5):
        assert signin(client, "ada", "wrong") == 401
    assert signin(client, "ada", "admin password") == 429


def test_removed_user_is_signed_out(client: TestClient, tmp_path: Path) -> None:
    signin(client, "obi", "observer password")
    assert client.get("/api/devices").status_code == 200
    config = tmp_path / "config.yaml"
    config.write_text(config.read_text().split("  obi:")[0])
    client.app.state.hub.reload()
    assert client.get("/api/devices").status_code == 401


def test_session_cookie_is_strict(client: TestClient) -> None:
    r = client.post("/api/auth/login", json={"user": "ada", "password": "admin password"}, headers=H)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_without_users_everyone_is_admin(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    write_config(config, users=False)
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.get("/api/devices").status_code == 200
        assert c.get("/api/auth/me").json() == {"signin": False, "user": None, "role": "admin"}
        assert c.post("/api/auth/login", json={"user": "x", "password": "y"}, headers=H).status_code == 400
