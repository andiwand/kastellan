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
    y = c.get("/api/devices/printer/yaml").json()
    assert y["text"].startswith("name: Office printer\n")
    text = y["text"] + "docker:\n  wud: http://192.168.1.9:3000\n"
    assert (
        c.put(
            "/api/devices/printer/yaml", json={"text": text, "version": y["version"]}, headers=H
        ).status_code
        == 200
    )
    assert "wud: http://192.168.1.9:3000" in config.read_text()
    again = {"text": "host: [", "version": y["version"]}
    assert c.put("/api/devices/printer/yaml", json=again, headers=H).status_code == 409  # stale
    y = c.get("/api/devices/printer/yaml").json()
    bad = {"text": "host: 192.168.1.9\nkind: toaster\n", "version": y["version"]}
    assert c.put("/api/devices/printer/yaml", json=bad, headers=H).status_code == 400
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


def test_settings_as_forms(hub_client) -> None:
    c, config, data = hub_client
    signin(c)
    got = c.get("/api/settings").json()
    s = got["sections"]
    assert s["general"]["check_interval"] == 60 and s["alarms"]["alarms"]["down_after"] == 180
    assert s["users"]["users"] == {"ada": {"role": "admin"}, "obi": {"role": "observer"}}  # no hashes
    v = got["version"]

    # A section saved: defaults left out, the rest of the file (and its comments) kept.
    r = c.put(
        "/api/settings/alarms",
        json={"value": {"alarms": {"down_after": 300, "cert_days": 14}}, "version": v},
        headers=H,
    )
    assert r.status_code == 200, r.text
    text = config.read_text()
    assert "alarms:\n  down_after: 300\n" in text and "cert_days" not in text
    assert "# the one from the ISP" in text
    v = r.json()["version"]

    # Notifications: a token is never sent to the page, and kept when the form leaves it.
    chans = [{"ntfy": "https://ntfy.sh/home", "token": "tk_secret", "min": "warning"}]
    v = c.put("/api/settings/notify", json={"value": {"notify": chans}, "version": v}, headers=H).json()[
        "version"
    ]
    shown = c.get("/api/settings").json()["sections"]["notify"]["notify"][0]
    assert shown["token"] == "__keep__" and "tk_secret" not in json.dumps(c.get("/api/settings").json())
    shown["min"] = "critical"
    v = c.put("/api/settings/notify", json={"value": {"notify": [shown]}, "version": v}, headers=H).json()[
        "version"
    ]
    assert "token: tk_secret" in config.read_text() and "min: critical" in config.read_text()

    # Users: a new one needs a password (hashed here); an old one keeps its hash.
    users = {"ada": {"role": "admin"}, "obi": {"role": "admin"}, "cy": {"role": "observer"}}
    assert (
        c.put("/api/settings/users", json={"value": {"users": users}, "version": v}, headers=H).status_code
        == 400
    )
    users["cy"]["password"] = "a long password"
    r = c.put("/api/settings/users", json={"value": {"users": users}, "version": v}, headers=H)
    assert r.status_code == 200, r.text
    text = config.read_text()
    assert ADMIN in text and "a long password" not in text and text.count("scrypt$") == 3
    v = r.json()["version"]

    # Nobody takes their own admin access away from a form either.
    users = {"ada": {"role": "observer"}, "obi": {"role": "admin"}, "cy": {"role": "observer"}}
    assert (
        c.put("/api/settings/users", json={"value": {"users": users}, "version": v}, headers=H).status_code
        == 400
    )

    # Groups of any devices; a wrong one is refused with where and why.
    groups = {"infra": {"name": "Infrastructure", "devices": ["router", "nas"]}}
    r = c.put("/api/settings/groups", json={"value": {"groups": groups}, "version": v}, headers=H)
    assert r.status_code == 200, r.text
    v = r.json()["version"]
    bad = {"infra": {"devices": ["nope"]}}
    r = c.put("/api/settings/groups", json={"value": {"groups": bad}, "version": v}, headers=H)
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    assert (
        c.put("/api/settings/general", json={"value": {"devices": {}}, "version": v}, headers=H).status_code
        == 400
    )
    assert (
        c.put("/api/settings/alarms", json={"value": {"alarms": {}}, "version": "old"}, headers=H).status_code
        == 409
    )

    # Secrets are only written.
    assert c.put("/api/secrets/wg-easy", json={"value": "pw"}, headers=H).status_code == 200
    assert (data / "secrets" / "wg-easy").read_text().strip() == "pw"
    assert c.get("/api/settings").json()["secrets"]["wg-easy"] is True


def test_a_service_reachable_from_the_internet_round_trips(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    form = c.get("/api/devices/router/form").json()
    form["checks"][1]["exposed"] = True
    r = c.put("/api/devices/router", json=form, headers=H)
    assert r.status_code == 200, r.text
    assert "exposed: true" in config.read_text().split("users:")[0]
    assert c.get("/api/devices/router/form").json()["checks"][1]["exposed"] is True
    assert [
        ch["exposed"] for ch in next(d for d in c.get("/api/devices").json() if d["id"] == "router")["checks"]
    ] == [False, True]


SECTIONS_TEXT = """check_interval: 60 # often enough
retention_days: 30

# --- alarms ---
alarms:
  down_after: 300 # minutes would be too long
  repeat: 60

# --- notify ---
notify:
  - ntfy: https://ntfy.sh/a
    min: warning

# --- groups ---
groups:
  aps: { devices: [a, b] }

users: {}
"""


def test_a_section_from_a_form_keeps_the_comments_around_it() -> None:
    t = SECTIONS_TEXT
    added = config_edit.set_top(t, "alarms", {"down_after": 300, "repeat": 60, "cert_days": 7})
    assert "  cert_days: 7\n\n# --- notify ---\nnotify:" in added
    assert "down_after: 300 # minutes would be too long" in added
    removed = config_edit.set_top(t, "alarms", {"down_after": 300})
    assert "  down_after: 300 # minutes would be too long\n\n# --- notify ---\nnotify:" in removed
    replaced = config_edit.set_top(t, "notify", [{"gotify": "https://g", "min": "info"}])
    assert "min: info\n\n# --- groups ---\ngroups:" in replaced
    same = config_edit.set_top(t, "groups", {"aps": {"devices": ["a", "b"]}})
    assert "devices: [a, b]" in same  # unchanged: still a flow list
    gone = config_edit.set_top(t, "notify", None)
    assert "notify" not in gone and "# --- groups ---\ngroups:" in gone
    gone = config_edit.set_top(t, "retention_days", None)
    assert "check_interval: 60 # often enough\n\n# --- alarms ---\nalarms:" in gone


def test_a_kept_secret_goes_back_only_to_its_own_channel(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    v = c.get("/api/settings").json()["version"]
    chans = [
        {"gotify": "https://gotify.home", "token": "GOTIFY_SECRET", "min": "warning"},
        {"ntfy": "https://ntfy.sh/home", "token": "NTFY_SECRET", "min": "warning"},
        {"email": "me@example.org", "smtp": "smtps://me:MAIL_SECRET@mail.example.org", "min": "critical"},
    ]
    v = c.put("/api/settings/notify", json={"value": {"notify": chans}, "version": v}, headers=H).json()[
        "version"
    ]
    got = c.get("/api/settings").json()
    shown = got["sections"]["notify"]["notify"]
    page = json.dumps(got)
    assert "SECRET" not in page and shown[2]["smtp"] == "smtps://me:__keep__@mail.example.org"

    # The first removed, the second pointed elsewhere: its token isn't carried along.
    moved = [dict(shown[1], ntfy="https://evil.example/t"), shown[2]]
    r = c.put("/api/settings/notify", json={"value": {"notify": moved}, "version": v}, headers=H)
    assert r.status_code == 400 and "token again" in r.json()["detail"]
    # Another kind in the same place: neither.
    other = [dict(shown[0], gotify=None, ntfy="https://gotify.home")]
    assert (
        c.put("/api/settings/notify", json={"value": {"notify": other}, "version": v}, headers=H).status_code
        == 400
    )
    # A channel without its id (new) gets no old secret.
    new = [{k: x for k, x in shown[1].items() if k != "_id"}]
    assert (
        c.put("/api/settings/notify", json={"value": {"notify": new}, "version": v}, headers=H).status_code
        == 400
    )
    # Reordered, each with its own id: each keeps its own.
    r = c.put(
        "/api/settings/notify", json={"value": {"notify": [shown[2], shown[1]]}, "version": v}, headers=H
    )
    assert r.status_code == 200, r.text
    text = config.read_text()
    assert "MAIL_SECRET" in text and "NTFY_SECRET" in text and "GOTIFY_SECRET" not in text
    assert text.index("MAIL_SECRET") < text.index("NTFY_SECRET")
    v = r.json()["version"]
    # A different mail server: its password must be typed again.
    shown = c.get("/api/settings").json()["sections"]["notify"]["notify"]
    shown[0]["smtp"] = "smtps://me:__keep__@other.example.org"
    assert (
        c.put("/api/settings/notify", json={"value": {"notify": shown}, "version": v}, headers=H).status_code
        == 400
    )
    # Secrets that aren't the forms' can't be set from here; a malformed body is a 400.
    assert c.put("/api/secrets/openwrt-router", json={"value": "x"}, headers=H).status_code == 403
    assert (
        c.put("/api/settings/users", json={"value": {"users": ["x"]}, "version": v}, headers=H).status_code
        == 400
    )


def test_the_vpn_form_cant_aim_at_kastellans_own_secrets(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    for name in ("group-fingerprints", "openwrt-router", "openwrt-other"):
        config.write_text(
            TEXT + f"vpn:\n  wireguard:\n    wg_easy: http://sv1:51821\n    password_secret: {name}\n"
        )
        with TestClient(create_app(Settings(config=config, data=tmp_path))) as c:
            signin(c)
            assert c.put(f"/api/secrets/{name}", json={"value": "x"}, headers=H).status_code == 403
    assert not (tmp_path / "secrets").exists()


def test_a_new_recipient_keeps_the_mail_password(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    v = c.get("/api/settings").json()["version"]
    chans = [{"email": "me@example.org", "smtp": "smtps://me:MAIL_SECRET@mail.example.org"}]
    v = c.put("/api/settings/notify", json={"value": {"notify": chans}, "version": v}, headers=H).json()[
        "version"
    ]
    shown = c.get("/api/settings").json()["sections"]["notify"]["notify"]
    shown[0]["email"] = "you@example.org"
    r = c.put("/api/settings/notify", json={"value": {"notify": shown}, "version": v}, headers=H)
    assert r.status_code == 200, r.text
    assert "MAIL_SECRET" in config.read_text() and "you@example.org" in config.read_text()


def test_a_device_as_text() -> None:
    text = """devices:
  # Routers
  router:
    host: 192.168.1.1 # the gateway
    # its web page
    checks:
      - ping

  # --- Servers ---
  nas:
    host: nas.lan
    docker:
      wud: http://nas.lan:3000
users:
  a: {role: admin}
"""
    assert config_edit.device_text(text, "router") == (
        "host: 192.168.1.1 # the gateway\n# its web page\nchecks:\n  - ping\n"
    )
    assert config_edit.set_device_text(text, "nas", config_edit.device_text(text, "nas")) == text
    changed = config_edit.set_device_text(text, "nas", "host: nas.lan\n")
    assert changed == text.replace("    docker:\n      wud: http://nas.lan:3000\n", "")
    changed = config_edit.set_device_text(text, "router", "host: 192.168.1.2\n")
    assert "\n\n  # --- Servers ---\n  nas:" in changed and "192.168.1.2" in changed
    with pytest.raises(KeyError):
        config_edit.device_text(text, "nope")


def test_a_site_and_a_dns_check_in_the_form(hub_client) -> None:
    c, config, _ = hub_client
    signin(c)
    body = {
        "id": "vps",
        "host": "203.0.113.20",
        "site": "hetzner",
        "checks": [
            {"type": "ping"},
            {"type": "dns", "target": "www.example.org", "expect": ["203.0.113.20"], "server": "9.9.9.9"},
        ],
    }
    assert c.post("/api/devices", json=body, headers=H).status_code == 200
    text = config.read_text()
    assert "site: hetzner" in text and "dns: www.example.org" in text and "expect: 203.0.113.20" in text
    f = c.get("/api/devices/vps/form").json()
    assert f["site"] == "hetzner" and f["checks"][1]["expect"] == ["203.0.113.20"]
    assert f["checks"][1]["server"] == "9.9.9.9" and f["checks"][1]["name"] == ""
    vps = next(d for d in c.get("/api/devices").json() if d["id"] == "vps")
    assert vps["site"] == "hetzner" and vps["site_name"] == "Hetzner"
    version = c.get("/api/settings").json()["version"]
    named = {"value": {"sites": {"hetzner": "Hetzner Cloud"}}, "version": version}
    assert c.put("/api/settings/sites", json=named, headers=H).status_code == 200
    vps = next(d for d in c.get("/api/devices").json() if d["id"] == "vps")
    assert vps["site_name"] == "Hetzner Cloud" and "hetzner: Hetzner Cloud" in config.read_text()
    f = c.get("/api/devices/vps/form").json()
    f.update(site="", checks=[{"type": "http", "target": "http://x", "expect": 999}])
    del f["id"]
    assert c.put("/api/devices/vps", json=f, headers=H).status_code == 422  # a status is 100 to 599


def test_a_device_as_text_keeps_its_comments_and_stays_its_own() -> None:
    text = """x: &c [ping]
devices:
  nas:
    host: 10.0.0.2
#    driver: synology
    checks:
      - ping
  # --- next ---
  r:
    host: 10.0.0.3
    checks: *c
"""
    block = config_edit.device_text(text, "nas")
    assert block == "host: 10.0.0.2\n#    driver: synology\nchecks:\n  - ping\n"
    # Saved back, the commented-out line moves in to the device's indent; nothing else changes.
    assert config_edit.set_device_text(text, "nas", block) == text.replace(
        "\n#    driver", "\n    #    driver"
    )
    # An anchor in one device's text must not change another device through an alias.
    with pytest.raises(ValueError):
        config_edit.set_device_text(text, "nas", "host: 10.0.0.2\nfoo: &c [tcp: 1]\n")


def test_a_device_key_removed() -> None:
    text = (
        "devices:\n  nas:\n    host: nas.lan # the NAS\n    collect: {}\n    checks:\n      - ping\n"
        "\n  # next\n  pi:\n    host: pi.lan\n"
    )
    out = config_edit.set_device_key(text, "nas", "collect", None)
    assert out == text.replace("    collect: {}\n", "")
