"""Backups that report in: their state, the alarms, and the routes (the token, the guard)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from kastellan.backups import Backups
from kastellan.config import Config

NOW = datetime(2026, 11, 21, 3, tzinfo=UTC)
CONFIG = Config.model_validate(
    {"devices": {"nas": {"host": "10.0.0.2"}}, "backups": {"b2": {"name": "NAS to B2", "device": "nas"}}}
)


def test_late_failed_and_fine(tmp_path: Path) -> None:
    b = Backups(tmp_path)
    assert b.problems(CONFIG, NOW) == []  # new: not due yet
    assert b.view(CONFIG, NOW)[0]["state"] == "waiting"
    [p] = b.problems(CONFIG, NOW + timedelta(hours=27))
    assert p.title == "nas: backup NAS to B2 never reported" and p.key == "backup:b2" and p.device == "nas"

    b.report("b2", True, "", NOW + timedelta(hours=28))
    assert b.problems(CONFIG, NOW + timedelta(hours=29)) == []
    [p] = b.problems(CONFIG, NOW + timedelta(hours=60))
    assert p.title == "nas: backup NAS to B2 hasn't run for 32 h"

    b.report("b2", False, "repository\nlocked", NOW + timedelta(hours=61))
    [p] = b.problems(CONFIG, NOW + timedelta(hours=61))
    assert p.title == "nas: backup NAS to B2 failed" and p.detail == "repository locked"
    b.report("b2", True, "", NOW + timedelta(hours=62))
    assert Backups(tmp_path).problems(CONFIG, NOW + timedelta(hours=62)) == []  # saved


def test_tokens(tmp_path: Path) -> None:
    b = Backups(tmp_path)
    token = b.new_token("b2", NOW)
    assert b.valid("b2", token) and not b.valid("b2", "x") and not b.valid("other", token)
    assert token not in (tmp_path / "backups.json").read_text()  # only its hash
    newer = b.new_token("b2", NOW)
    assert not b.valid("b2", token) and b.valid("b2", newer)


def test_config_is_checked() -> None:
    with pytest.raises(ValidationError):
        Config.model_validate({"devices": {}, "backups": {"b2": {"device": "nas"}}})
    with pytest.raises(ValidationError):
        Config.model_validate({"devices": {"nas": {"host": "x"}}, "backups": {"B 2": {"device": "nas"}}})


def test_the_routes(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from kastellan.api import create_app
    from kastellan.auth import hash_password
    from kastellan.settings import Settings

    path = tmp_path / "config.yaml"
    path.write_text(
        "devices:\n  nas:\n    host: 127.0.0.1\nbackups:\n  b2:\n    device: nas\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password('admin password')}\n"
    )
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        assert c.get("/api/backups").status_code == 401
        # The job's call: no session, no X-Kastellan; its token only.
        assert c.post("/api/backups/b2/ok").status_code == 401
        h = {"X-Kastellan": "1"}
        c.post("/api/auth/login", json={"user": "ada", "password": "admin password"}, headers=h)
        response = c.post("/api/backups/b2/token", headers=h)
        assert response.headers["cache-control"] == "no-store"
        token = response.json()["token"]
        version = c.get("/api/settings").json()["version"]
        saved = c.put(
            "/api/settings/backups",
            headers=h,
            json={"version": version, "value": {"backups": {"b2": {"device": "nas", "within_hours": 26}}}},
        )
        assert saved.status_code == 200, saved.text
        c.cookies.clear()  # the job has no session
        auth = {"Authorization": f"Bearer {token}"}
        assert c.post("/api/backups/b2/fail", content=b"x" * 2001, headers=auth).status_code == 413
        assert c.post("/api/backups/b2/fail", content=b"disk full", headers=auth).status_code == 200
        assert c.post("/api/backups/b2/ok", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/api/backups/b2/ok", headers=auth).status_code == 401  # only POST passes the guard
        c.post("/api/auth/login", json={"user": "ada", "password": "admin password"}, headers=h)
        [b] = c.get("/api/backups").json()
        assert b["state"] == "failed" and b["message"] == "disk full" and b["token"] is True
        assert "hash" not in str(b) and token not in str(b)
    assert "backup.token" in (tmp_path / "audit.log").read_text()


def test_report_order_wins_within_one_second(tmp_path: Path) -> None:
    b = Backups(tmp_path)
    b.report("b2", True, "", NOW)
    b.report("b2", False, "failed", NOW)
    assert Backups(tmp_path).view(CONFIG, NOW)[0]["state"] == "failed"
    b.report("b2", True, "", NOW)
    assert Backups(tmp_path).view(CONFIG, NOW)[0]["state"] == "ok"


def test_failed_write_keeps_the_previous_state(tmp_path: Path, monkeypatch) -> None:
    import kastellan.backups as module

    b = Backups(tmp_path)
    token = b.new_token("b2", NOW)
    b.report("b2", False, "failed", NOW)

    def full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(module.os, "replace", full)
    with pytest.raises(OSError):
        b.new_token("b2", NOW)
    assert b.valid("b2", token)
    with pytest.raises(OSError):
        b.report("b2", True, "", NOW)
    assert b.view(CONFIG, NOW)[0]["state"] == "failed"
    assert not list(tmp_path.glob(".backups-*"))


def test_removed_and_reassigned_jobs_lose_their_tokens(tmp_path: Path) -> None:
    b = Backups(tmp_path)
    b.sync(CONFIG)
    token = b.new_token("b2", NOW)
    b.sync(Config())
    b.sync(CONFIG)
    assert not b.valid("b2", token)
    token = b.new_token("b2", NOW)
    other = Config.model_validate({"devices": {"pi": {"host": "x"}}, "backups": {"b2": {"device": "pi"}}})
    b.sync(other)
    assert not b.valid("b2", token)


@pytest.mark.parametrize(
    "data", ["[]", '{"b2": null}', '{"b2": {"since": "bad"}}', '{"b2": {"since": "2026-01-01"}}']
)
def test_invalid_saved_state_is_ignored(tmp_path: Path, data: str) -> None:
    (tmp_path / "backups.json").write_text(data)
    assert Backups(tmp_path).view(CONFIG, NOW)[0]["state"] == "waiting"
