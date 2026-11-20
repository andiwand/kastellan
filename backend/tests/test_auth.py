import json
from pathlib import Path

from kastellan.audit import Audit
from kastellan.auth import SESSION_IDLE, Brake, Sessions, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "not a hash")
    assert hash_password("same") != hash_password("same")  # salted


def test_sessions_hashed_on_disk_and_expire(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    sessions = Sessions(path)
    token = sessions.create("andreas")
    assert sessions.user(token) == "andreas"
    assert token not in path.read_text()  # only the hash is stored
    assert path.stat().st_mode & 0o077 == 0
    assert Sessions(path).user(token) == "andreas"  # survives a restart
    created = json.loads(path.read_text()).popitem()[1]["used"]
    assert sessions.user(token, now=created + SESSION_IDLE + 1) is None
    assert sessions.user(token) is None


def test_sessions_end(tmp_path: Path) -> None:
    sessions = Sessions(tmp_path / "s.json")
    a, b = sessions.create("a"), sessions.create("b")
    sessions.end(a)
    assert sessions.user(a) is None
    sessions.end_all_except({"a"})
    assert sessions.user(b) is None
    assert sessions.user(None) is None and sessions.user("nonsense") is None


def test_brake() -> None:
    brake = Brake(limit=3, window=60)
    for t in (0, 1, 2):
        assert brake.blocked("ip", now=t) == 0
        brake.failed("ip", now=t)
    assert brake.blocked("ip", now=3) == 57
    assert brake.blocked("other", now=3) == 0
    assert brake.blocked("ip", now=61) == 0
    brake.failed("ip", now=62)
    brake.succeeded("ip")
    assert brake.blocked("ip", now=63) == 0


def test_audit(tmp_path: Path) -> None:
    audit = Audit(tmp_path / "audit.log")
    audit.record("signin", "andreas", "10.8.0.3")
    audit.record("terminal.open", "andreas", "10.8.0.3", device="sv1")
    with (tmp_path / "audit.log").open("a") as f:
        f.write('{"broken')  # a crash mid-line
    entries = audit.recent()
    assert [e["action"] for e in entries] == ["terminal.open", "signin"]
    assert entries[0]["device"] == "sv1"
    assert (tmp_path / "audit.log").stat().st_mode & 0o077 == 0
