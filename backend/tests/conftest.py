import contextlib
import functools
from pathlib import Path

import pytest

from kastellan import checks


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failed checks are retried after a pause; tests don't wait for it."""
    monkeypatch.setattr(checks, "RETRY_AFTER", 0)


@pytest.fixture(autouse=True)
def _test_host_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test client calls itself testserver (and hub.lan in some tests); let the guard's host
    check (against DNS rebinding) accept those names."""
    from kastellan import auth_api

    monkeypatch.setattr(auth_api, "LOCAL_NAMES", {"localhost", "testserver", "hub.lan"})


@pytest.fixture(autouse=True)
def _quick_key_rings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Key ring slots use a strong scrypt (hundreds of ms each); tests use a light one."""
    from kastellan import vault

    monkeypatch.setattr(vault, "_N", 2**12)


ADMIN_PASSWORD = "pw for tests"


@functools.cache
def password_hash(password: str = ADMIN_PASSWORD) -> str:
    """A config.yaml password hash, made once per password (each takes ~100 ms)."""
    from kastellan.auth import hash_password

    return hash_password(password)


@pytest.fixture
def admin_client(tmp_path: Path):
    """`admin_client(config_text, **settings)`: the hub on `<tmp_path>/config.yaml` (data in
    tmp_path unless `data=` says otherwise), with an admin `ada` (password ADMIN_PASSWORD) added
    unless the text has its own `users:`, signed in as ada (`signin=False`: not). The client
    sends X-Kastellan with every request; it closes when the test ends."""
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.settings import Settings

    with contextlib.ExitStack() as stack:

        def make(
            config: str = "devices: {}\n",
            *,
            signin: bool = True,
            base_url: str = "http://testserver",
            **settings,
        ) -> TestClient:
            if "users:" not in config:
                config += f"users:\n  ada:\n    role: admin\n    password: {password_hash()}\n"
            path = tmp_path / "config.yaml"
            path.write_text(config)
            settings.setdefault("data", tmp_path)
            app = create_app(Settings(config=path, **settings))
            c = stack.enter_context(TestClient(app, base_url=base_url, headers={"X-Kastellan": "1"}))
            if signin:
                r = c.post("/api/auth/login", json={"user": "ada", "password": ADMIN_PASSWORD})
                assert r.status_code == 200, r.text
            return c

        yield make


@pytest.fixture(scope="session")
def demo_client(tmp_path_factory: pytest.TempPathFactory):
    """The demo hub (a made-up network), started once for all tests that only look at it."""
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.settings import Settings

    data = tmp_path_factory.mktemp("demo")
    with TestClient(create_app(Settings(config=data / "none.yaml", data=data, demo=True))) as c:
        yield c
