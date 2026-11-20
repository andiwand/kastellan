"""Changing config.yaml from the app: in place (comments kept), checked, guarded, backed up."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kastellan import config_edit
from kastellan.api import create_app
from kastellan.auth import hash_password
from kastellan.config import Config
from kastellan.settings import Settings

H = {"X-Kastellan": "1"}
ADMIN = hash_password("admin password")
OBSERVER = hash_password("observer password")

TEXT = f"""# My network.
check_interval: 60

devices:
  router:
    name: Router # the one from the ISP
    host: 192.168.1.1
    kind: router
    checks:
      - ping
      - http: https://192.168.1.1 # its page
        name: web
  nas:
    host: nas.lan
    parent: router
    ssh:
      user: admin
    tags: [docker]

users:
  ada:
    role: admin
    password: {ADMIN}
  obi:
    role: observer
    password: {OBSERVER}
"""


def form(**kw):
    base = {
        "name": "Router",
        "host": "192.168.1.1",
        "kind": "router",
        "tags": [],
        "always_on": None,
        "exposed": False,
        "checks": [
            {"type": "ping", "target": None, "name": "ping", "timeout": 5},
            {"type": "http", "target": "https://192.168.1.1", "name": "web", "timeout": 5},
        ],
    }
    return base | kw


def test_a_device_edit_keeps_the_rest_of_the_file() -> None:
    new = config_edit.edit_device(TEXT, "router", form(name="Main router", tags=["isp"]))
    assert "# My network." in new and "# the one from the ISP" in new and "# its page" in new
    assert "name: Main router" in new and "tags: [isp]" in new
    assert "timeout" not in new and "name: ping" not in new  # defaults stay unwritten
    assert new.count("\n") == TEXT.count("\n") + 1  # only the tags line was added
    Config.model_validate(__import__("ruamel.yaml").yaml.YAML(typ="safe").load(new))


def test_checks_added_changed_removed() -> None:
    checks = [
        {"type": "tcp", "target": "22", "name": "tcp:22"},
        {"type": "http", "target": "https://router.example.org", "name": "public", "tls": True},
    ]
    new = config_edit.edit_device(TEXT, "router", form(checks=checks))
    assert "- tcp: 22\n" in new and "tls: true" in new and "# its page" not in new
    assert "name: tcp:22" not in new


def test_add_and_delete_devices() -> None:
    added = config_edit.edit_device(
        TEXT,
        "printer",
        form(name="Printer", host="192.168.1.9", kind="printer", checks=[{"type": "ping"}]),
        new=True,
    )
    # Pinged by default: no checks written; the blank line before users: stays.
    assert "  printer:\n    name: Printer\n    host: 192.168.1.9\n    kind: printer\n\nusers:" in added
    with pytest.raises(ValueError, match="already"):
        config_edit.edit_device(TEXT, "nas", form(), new=True)
    with pytest.raises(ValueError, match="lowercase"):
        config_edit.edit_device(TEXT, "Bad Id", form(), new=True)
    gone = config_edit.edit_device(TEXT, "router", None)
    assert "router:" not in gone and "parent:" not in gone  # nothing hangs off what is gone
    assert "ssh:\n      user: admin" in gone  # the rest of nas stays


def test_an_edit_keeps_what_the_form_does_not_cover() -> None:
    new = config_edit.edit_device(
        TEXT, "nas", form(name="", host="nas.lan", kind="server", tags=["docker"], checks=[{"type": "ping"}])
    )
    assert "ssh:\n      user: admin" in new and "parent: router" in new
    assert "kind: server" in new and "name:" not in new.split("nas:")[1].split("users:")[0]


@pytest.fixture
def hub_client(tmp_path: Path):
    config = tmp_path / "config.yaml"
    config.write_text(TEXT)
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        yield c, config, tmp_path


def signin(c: TestClient, user: str = "ada", password: str = "admin password") -> None:
    assert c.post("/api/auth/login", json={"user": user, "password": password}, headers=H).status_code == 200


def test_the_editor(hub_client) -> None:
    c, config, data = hub_client
    assert c.get("/api/config").status_code == 401
    signin(c, "obi", "observer password")
    assert c.get("/api/config").status_code == 403  # observers don't see it (password hashes)
    signin(c)
    got = c.get("/api/config").json()
    assert got["text"] == TEXT

    # Checked, not saved: a mistake says where it is.
    bad = c.post("/api/config/check", json={"text": "devices: [\n"}, headers=H).json()
    assert not bad["ok"] and bad["error"].startswith("line ")
    wrong = c.put(
        "/api/config",
        json={"text": TEXT.replace("kind: router", "kind: fridge"), "version": got["version"]},
        headers=H,
    )
    assert wrong.status_code == 400 and "kind" in wrong.json()["detail"]
    assert config.read_text() == TEXT

    # Saved, loaded at once, the old one kept, audited (without the password hashes).
    new = TEXT.replace("check_interval: 60", "check_interval: 30")
    r = c.put("/api/config", json={"text": new, "version": got["version"]}, headers=H)
    assert r.status_code == 200 and config.read_text() == new
    assert [p.read_text() for p in (data / "config-backups").iterdir()] == [TEXT]
    audit = [json.loads(line) for line in (data / "audit.log").read_text().splitlines()]
    assert audit[-1]["action"] == "config.file" and "check_interval: 30" in audit[-1]["changes"]

    # Saving over a change made in the meantime fails.
    stale = c.put("/api/config", json={"text": TEXT, "version": got["version"]}, headers=H)
    assert stale.status_code == 409 and config.read_text() == new

    # Nobody takes their own admin access, or removes every user.
    version = c.get("/api/config").json()["version"]
    demoted = new.replace("ada:\n    role: admin", "ada:\n    role: observer")
    assert c.put("/api/config", json={"text": demoted, "version": version}, headers=H).status_code == 400
    no_users = new.split("users:")[0]
    r = c.put("/api/config", json={"text": no_users, "version": version}, headers=H)
    assert r.status_code == 400 and "lock" in r.json()["detail"]


def test_device_forms(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    body = {
        "id": "printer",
        "name": "Printer",
        "host": "192.168.1.9",
        "kind": "printer",
        "checks": [{"type": "ping"}, {"type": "tcp", "target": 631, "name": "ipp"}],
    }
    assert c.post("/api/devices", json=body, headers=H).status_code == 200
    assert "printer" in [d["id"] for d in c.get("/api/devices").json()]  # loaded at once
    f = c.get("/api/devices/printer/form").json()
    assert f["name"] == "Printer" and [x["name"] for x in f["checks"]] == ["", "ipp"]
    assert f["always_on"] is None  # not in the file: by kind, and not written back
    assert c.get("/api/devices/nas/form").json()["name"] == ""  # the id stands in, unwritten

    f["name"] = "Office printer"
    del f["id"]
    assert c.put("/api/devices/printer", json=f, headers=H).status_code == 200
    assert "name: Office printer" in config.read_text() and "always_on" not in config.read_text()

    # The form's version is stale now: saving it again is refused.
    stale = c.put("/api/devices/printer", json=f | {"name": "Again"}, headers=H)
    assert stale.status_code == 409
    f = c.get("/api/devices/printer/form").json()
    del f["id"]
    bad = {**f, "checks": [{"type": "tcp", "target": "x"}]}
    assert c.put("/api/devices/printer", json=bad, headers=H).status_code == 400
    assert c.delete("/api/devices/printer", headers=H).status_code == 200
    assert "printer" not in config.read_text()
    assert c.delete("/api/devices/printer", headers=H).status_code == 404


def test_not_while_signin_is_off(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("devices:\n  box:\n    host: 127.0.0.1\n")
    with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
        r = c.post("/api/devices", json={"id": "x", "host": "1.2.3.4"}, headers=H)
        assert r.status_code == 403 and "sign-in" in r.json()["detail"]
        assert c.get("/api/config").status_code == 403


LAYOUT = """devices:
  # --- the router ---
  router:
    host: 192.168.1.1
    checks:
      - ping # always answers
      - tcp: 22

  # --- the NAS ---
  nas:
    host: nas.lan
    checks: [] # graph only

  # --- the last one ---
  cam:
    host: cam.lan
    kind: camera

# Sign-in: users
users: {}
"""


def device_form(**kw):
    return {
        "name": "",
        "host": "x",
        "kind": "other",
        "tags": [],
        "always_on": None,
        "exposed": False,
        "checks": [{"type": "ping", "name": "ping", "timeout": 5}],
    } | kw


def test_comments_around_devices_stay_where_they_belong() -> None:
    router = device_form(
        host="192.168.1.1",
        checks=[
            {"type": "ping", "target": None, "name": "", "timeout": 5},
            {"type": "tcp", "target": 22, "name": "", "timeout": 5},
        ],
    )
    # Only the name changes: the checks stay exactly as written, comments and all.
    new = config_edit.edit_device(LAYOUT, "router", router | {"name": "Router"})
    assert "      - ping # always answers\n      - tcp: 22\n\n  # --- the NAS ---\n  nas:" in new
    assert "  router:\n    name: Router\n    host: 192.168.1.1\n" in new  # in its place
    # Its last check removed: the NAS header stays above the NAS.
    new = config_edit.edit_device(LAYOUT, "router", router | {"checks": router["checks"][:1]})
    assert "      - ping # always answers\n\n  # --- the NAS ---\n  nas:" in new
    # A field added to the last device lands inside it, not under "# Sign-in: users".
    new = config_edit.edit_device(LAYOUT, "cam", device_form(host="cam.lan", kind="camera", tags=["outside"]))
    assert "    kind: camera\n    tags: [outside]\n\n# Sign-in: users\nusers:" in new


def test_checks_empty_stays_empty() -> None:
    new = config_edit.edit_device(LAYOUT, "nas", device_form(host="nas.lan", name="NAS", checks=[]))
    assert "    checks: [] # graph only" in new and "name: NAS" in new


def test_deleting_keeps_the_neighbours_headers() -> None:
    new = config_edit.edit_device(LAYOUT, "nas", None)
    assert "--- the NAS ---" not in new
    assert "      - tcp: 22\n\n  # --- the last one ---\n  cam:" in new
    new = config_edit.edit_device(LAYOUT, "cam", None)
    assert "    checks: [] # graph only\n\n# Sign-in: users\nusers:" in new


def test_a_changed_target_drops_the_old_default_name() -> None:
    router = device_form(
        host="192.168.1.1",
        checks=[
            {"type": "ping", "target": None, "name": "", "timeout": 5},
            {"type": "tcp", "target": 2222, "name": "", "timeout": 5},
        ],
    )
    new = config_edit.edit_device(LAYOUT, "router", router)
    assert "      - tcp: 2222\n" in new and "name:" not in new.split("nas:")[0]


def test_errors_never_quote_the_file(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    doubled = TEXT.replace("    password: " + ADMIN, "    password: " + ADMIN + "\n    password: " + ADMIN)
    r = c.post("/api/config/check", json={"text": doubled}, headers=H).json()
    assert not r["ok"] and "appears twice" in r["error"] and "scrypt" not in r["error"]
    # A device form on a broken file: a 400 that says where, not a 500.
    config.write_text(TEXT + "devices:\n  x:\n    host: y\n")
    f = form(name="R")
    r = c.put("/api/devices/router", json=f, headers=H)
    assert r.status_code == 400 and "appears twice" in r.json()["detail"]


def test_write_keeps_a_symlink_and_the_backups_private(tmp_path: Path) -> None:
    real = tmp_path / "real.yaml"
    real.write_text("a: 1\n")
    link = tmp_path / "config.yaml"
    link.symlink_to(real)
    config_edit.write(link, tmp_path / "backups", "a: 1\n", "a: 2\n")
    assert link.is_symlink() and real.read_text() == "a: 2\n"
    assert oct((tmp_path / "backups").stat().st_mode & 0o777) == "0o700"
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in (tmp_path / "backups").iterdir())


def test_the_audit_diff_masks_secrets() -> None:
    old = "notify:\n  - ntfy: https://ntfy.sh/secret-topic\n    token: tk_abc\nvulns:\n  nvd_api_key: k1\n"
    new = old.replace("tk_abc", "tk_def").replace("k1", "k2").replace("secret-topic", "other-topic")
    d = config_edit.diff(old, new)
    for secret in ("tk_abc", "tk_def", "k1", "k2", "secret-topic", "other-topic"):
        assert secret not in d
    assert d == "(only secrets, comments or layout changed)"


def test_masking_any_yaml_style() -> None:
    old = "users:\n  me: {role: admin, password: scrypt$old}\nnotify:\n  - {email: a@b.c, smtp: 'smtps://u:hunter2@m.example'}\n"
    new = (
        "users:\n  me: {role: admin, password: scrypt$new}\nnotify:\n  - email: a@b.c\n    smtp: >-\n"
        "      smtps://u:hunter3@m.example\ndevices:\n  x:\n    host: h\n    checks:\n      - http: https://admin:pw@h/\n"
    )
    d = config_edit.diff(old, new)
    for secret in ("scrypt$old", "scrypt$new", "hunter2", "hunter3", "admin:pw"):
        assert secret not in d
    assert "https://***@h/" in d


def test_deleting_a_device_takes_it_out_of_its_groups() -> None:
    from kastellan.config_edit import edit_device

    text = """devices:
  a: { host: 10.0.0.1, openwrt: {} }
  b: { host: 10.0.0.2, openwrt: {} }
groups:
  aps: { devices: [a, b] }
  solo: { devices: [b] }
"""
    out = edit_device(text, "b", None)
    assert "solo" not in out and "devices: [a]" in out
    out = edit_device(out, "a", None)
    assert "groups" not in out
