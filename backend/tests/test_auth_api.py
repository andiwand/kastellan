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


# --- Fail closed (security review, findings 1, 2, 3, 6, 7, 12) ---


def test_broken_config_at_start_locks_instead_of_opening(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("devices:\n  box:\n    host: [oops\n")  # a YAML typo
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert c.get("/api/devices").status_code == 503
        assert c.get("/api/auth/me").status_code == 503
        health = c.get("/api/health").json()
        assert health["locked"] and health["config_error"] == "config.yaml has a problem"


def test_deleting_or_emptying_config_keeps_signin(client: TestClient, tmp_path: Path) -> None:
    hub = client.app.state.hub
    config = tmp_path / "config.yaml"
    text = config.read_text()
    for broken in ("", None, "devices: {}\n"):  # emptied, deleted, users removed
        if broken is None:
            config.unlink()
        else:
            config.write_text(broken)
        hub.reload()
        assert client.get("/api/devices").status_code == 401, broken
        assert client.get("/api/auth/me").json()["signin"] is True
        config.write_text(text)
        hub.reload()


def test_plain_password_never_shows(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("users:\n  ada:\n    role: admin\n    password: MyPlainSecret99\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        assert "MyPlainSecret99" not in c.get("/api/health").text
    assert "MyPlainSecret99" not in client_log(tmp_path)


def client_log(tmp_path: Path) -> str:
    return (tmp_path / "audit.log").read_text() if (tmp_path / "audit.log").exists() else ""


def test_new_password_ends_old_sessions(client: TestClient, tmp_path: Path) -> None:
    signin(client, "ada", "admin password")
    assert client.get("/api/devices").status_code == 200
    config = tmp_path / "config.yaml"
    config.write_text(config.read_text().replace(ADMIN_HASH, hash_password("a new password")))
    client.app.state.hub.reload()
    assert client.get("/api/devices").status_code == 401


def test_weak_hash_and_huge_names_are_refused(client: TestClient) -> None:
    from pydantic import ValidationError

    from kastellan.config import User

    with pytest.raises(ValidationError):
        User(password="scrypt$2$1$1$c2FsdA==$a2V5")
    r = client.post("/api/auth/login", json={"user": "x" * 100_000, "password": "y"}, headers=H)
    assert r.status_code == 422


async def test_parallel_guesses_hit_the_brake(tmp_path: Path) -> None:
    import asyncio

    import httpx

    config = tmp_path / "config.yaml"
    write_config(config)
    app = create_app(Settings(config=config, data=tmp_path))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            guesses = [
                c.post("/api/auth/login", json={"user": "ada", "password": f"g{i}"}, headers=H)
                for i in range(20)
            ]
            codes = [r.status_code for r in await asyncio.gather(*guesses)]
    assert codes.count(401) == 5 and codes.count(429) == 15


def test_rebound_names_are_refused(client: TestClient) -> None:
    from kastellan.auth_api import allowed_host

    assert client.get("/api/health", headers={"host": "evil.example:8100"}).status_code == 421
    assert allowed_host("192.168.15.10:8100", ())
    assert allowed_host("[fd00::1]:8100", ())
    assert allowed_host("kastellan.lan", ("kastellan.lan",))
    assert not allowed_host("kastellan.lan", ())


def test_unwritable_marker_locks(tmp_path: Path) -> None:
    import os

    config = tmp_path / "config.yaml"
    write_config(config)
    data = tmp_path / "data"
    data.mkdir()
    os.chmod(data, 0o500)  # read-only: the marker can't be written
    try:
        with TestClient(create_app(Settings(config=config, data=data))) as c:
            assert c.get("/api/devices").status_code == 503
    finally:
        os.chmod(data, 0o700)


def test_acknowledge_alarm_with_slashes(client: TestClient) -> None:
    """Security/alarms review: keys of HTTP checks contain slashes and couldn't be acknowledged."""
    from datetime import UTC, datetime

    from kastellan.alarms import Problem, Severity

    hub = client.app.state.hub
    key = "service:box:http:http://box/x"
    hub.alarms.update(
        [Problem(key, Severity.WARNING, "box", "box: web", "box: web is down")], datetime.now(UTC)
    )
    signin(client, "ada", "admin password")
    r = client.post(f"/api/alarms/{key}/ack", json={"note": "looking"}, headers=H)
    assert r.status_code == 200 and r.json()["acknowledged_by"] == "ada"


def test_signing_in_elsewhere_doesnt_wipe_the_guesses(client: TestClient) -> None:
    """Five wrong guesses at a login brake it, the right password too; another login's own
    sign-in in between doesn't reset the count."""
    for _ in range(4):
        assert signin(client, "ada", "nope") == 401
    assert signin(client, "obi", "observer password") == 200  # a login of one's own
    assert signin(client, "ada", "nope") == 401
    assert signin(client, "ada", "admin password") == 429  # still five guesses at ada


def test_only_the_hubs_own_page_may_ask(client: TestClient) -> None:
    r = client.get("/api/health", headers={"Sec-Fetch-Site": "same-site"})
    assert r.status_code == 403  # a device page on a sibling name
    r = client.get("/api/health", headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200
    assert r.headers["x-frame-options"] == "DENY" and r.headers["origin-agent-cluster"] == "?1"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
