import json
from pathlib import Path

import asyncssh
import pytest
from fastapi.testclient import TestClient

from kastellan.api import create_app
from kastellan.auth import hash_password, password_tag
from kastellan.config import VaultSettings
from kastellan.secrets import Secrets
from kastellan.settings import Settings
from kastellan.ssh import Keys, Target
from kastellan.vault import KASTELLAN, PEOPLE, Locked, NotForKastellan, Vault, WrongKey, new_ring_key, policy

H = {"X-Kastellan": "1"}
ADA, BEA, OBI = "ada password", "bea password", "obi password"


def test_the_crypto(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    assert v.state == "absent"
    recovery = v.create("ada", ADA, "t1")
    assert set(recovery) == {KASTELLAN, PEOPLE} and recovery[KASTELLAN] != recovery[PEOPLE]
    v.put("router", "s3cret", name="Router")
    assert v.entry("router").ring == KASTELLAN and v.get("router") == "s3cret"
    text = (tmp_path / "vault.json").read_text()
    assert "s3cret" not in text and ADA not in text
    assert all(r.replace("-", "") not in text for r in recovery.values())
    assert oct((tmp_path / "vault.json").stat().st_mode)[-3:] == "600"

    v.lock()
    assert v.state == "locked"
    with pytest.raises(Locked):
        v.get("router")
    assert v.unlock("ada", "wrong") == []  # her slots, but the password opens none
    with pytest.raises(WrongKey):
        v.unlock("obi", ADA)  # nobody else's slot opens with it
    assert sorted(v.unlock("ada", ADA)) == [KASTELLAN, PEOPLE]
    assert v.get("router") == "s3cret"
    v.lock()
    with pytest.raises(WrongKey):
        v.unlock_recovery(KASTELLAN, recovery[PEOPLE])  # each ring its own recovery key
    v.unlock_recovery(KASTELLAN, recovery[KASTELLAN].lower())  # with or without dashes
    assert v.state == "partly" and v.get("router") == "s3cret"


def test_tampering_is_noticed(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.put("mine", "x", ring=PEOPLE)
    doc = json.loads(v.path.read_text())
    # Moved by hand into Kastellan's ring, to make Kastellan use a person's login: it won't open.
    doc["rings"][KASTELLAN]["entries"]["mine"] = doc["rings"][PEOPLE]["entries"].pop("mine")
    v.path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="decrypt"):
        v.get("mine")
    slots = doc["rings"][KASTELLAN]["slots"]
    slots[0]["data"] = slots[1]["data"]
    v.path.write_text(json.dumps(doc))
    v.lock()
    assert KASTELLAN not in v.unlock("ada", ADA)


def test_a_persons_login_is_never_used_by_kastellan(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.put("my-root", "pw", ring=PEOPLE)
    with pytest.raises(NotForKastellan):
        v.get("my-root")  # background use
    assert v.get("my-root", by="ada") == "pw"
    secrets = Secrets(tmp_path / "secrets", v)
    with pytest.raises(NotForKastellan):
        secrets.get("my-root")


def test_slots_follow_the_admins(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t1")
    assert v.keep_slot("bea", BEA, "t2", {"ada", "bea"})  # bea signed in while it was unlocked
    assert not v.keep_slot("bea", BEA, "t2", {"ada", "bea"})  # nothing to change
    v.lock()
    v.unlock("bea", BEA)
    assert v.keep_slot("bea", "bea's new password", "t3", {"bea"})  # changed; ada no admin now
    assert v.has_slot(KASTELLAN, "bea", "t3") and not v.has_slot(KASTELLAN, "ada", "t1")
    v.lock()
    assert v.unlock("bea", BEA) == []
    assert v.unlock("bea", "bea's new password") == [KASTELLAN, PEOPLE]


def test_secrets_and_keys_in_the_key_ring(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    keys = Keys(tmp_path / "ssh", v)
    v.create("ada", ADA, "t")
    hub_key = keys.hub_key()
    public = keys.public_key()
    collect = keys.public_key("collect")
    assert not (tmp_path / "ssh" / "hub_ed25519").exists()  # in the key ring, not a file
    assert (tmp_path / "ssh" / "collect_ed25519").exists()  # read-only: a file, works locked
    v.lock()
    assert keys.public_key() == public  # setup commands still show it
    assert keys.public_key("collect") == collect
    with pytest.raises(Locked):
        keys.hub_key()
    v.unlock("ada", ADA)
    nxt = keys.make_next()
    keys.promote_next(nxt.get_fingerprint())
    assert keys.hub_key().get_fingerprint() == nxt.get_fingerprint()
    assert keys.side_key("old").get_fingerprint() == hub_key.get_fingerprint()
    assert keys.public_key() != public


async def test_a_locked_key_ring_doesnt_log_in(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    keys = Keys(tmp_path / "ssh", v)
    v.create("ada", ADA, "t")
    v.lock()
    with pytest.raises(Locked):
        await keys.connect(Target("127.0.0.1", 1, "root"))


def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "devices:\n  box:\n    host: 127.0.0.1\n    openwrt: {}\n"
        f"users:\n  ada:\n    role: admin\n    password: {hash_password(ADA)}\n"
        f"  bea:\n    role: admin\n    password: {hash_password(BEA)}\n"
        f"  obi:\n    role: observer\n    password: {hash_password(OBI)}\n"
    )
    return path


def signin(c: TestClient, user: str, password: str) -> None:
    assert c.post("/api/auth/login", json={"user": user, "password": password}, headers=H).status_code == 200


def test_making_it_takes_the_files_in(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "openwrt-box").write_text("router pw")
    keys = Keys(tmp_path / "ssh")
    hub_fp = keys.hub_key().get_fingerprint()
    collect = keys.public_key("collect")
    audit = tmp_path / "audit.log"
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "obi", OBI)
        assert c.get("/api/vault").status_code == 403
        assert c.post("/api/vault/create", json={"password": OBI}, headers=H).status_code == 403
        signin(c, "ada", ADA)
        state = c.get("/api/vault").json()
        assert state["state"] == "absent" and state["files"] == ["openwrt-box"]
        assert c.post("/api/vault/create", json={"password": "wrong"}, headers=H).status_code == 403
        made = c.post("/api/vault/create", json={"password": ADA}, headers=H).json()
        assert "openwrt-box" in made["taken"] and "ssh/hub" in made["taken"]
        assert set(made["recovery"]) == {KASTELLAN, PEOPLE}
        assert not (secrets / "openwrt-box").exists() and not (tmp_path / "ssh" / "hub_ed25519").exists()
        hub = c.app.state.hub
        assert hub.secrets.get("openwrt-box") == "router pw"
        assert hub.keys.hub_key().get_fingerprint() == hub_fp
        assert hub.keys.public_key("collect") == collect

        state = c.get("/api/vault").json()
        assert "router pw" not in json.dumps(state)
        assert {"openwrt-box", "ssh/hub"} <= {e["id"] for e in state["entries"]}
        assert next(e for e in state["entries"] if e["id"] == "openwrt-box")["used_by"] == [
            "box: its OpenWrt login"
        ]

        # A person's own login: stored, shown only on purpose, never deleted while unused... freely.
        r = c.post(
            "/api/vault/entries",
            json={"name": "NAS root", "ring": PEOPLE, "user": "root", "secret": "nas pw"},
            headers=H,
        )
        assert r.json()["id"] == "nas-root"
        assert (
            c.post("/api/vault/entries/nas-root/reveal", json={"password": "x"}, headers=H).status_code == 403
        )
        shown = c.post("/api/vault/entries/nas-root/reveal", json={"password": ADA}, headers=H).json()
        assert shown == {"secret": "nas pw"}
        never = c.post("/api/vault/entries/ssh/hub/reveal", json={"password": ADA}, headers=H)
        assert never.status_code == 400  # a private key is never shown
        assert c.delete("/api/vault/entries/openwrt-box", headers=H).status_code == 400  # in use
        assert c.delete("/api/vault/entries/nas-root", headers=H).status_code == 200

        # Locked: what needs a secret says so (423), never a 500.
        assert c.post("/api/vault/lock", json={}, headers=H).json() == {"state": "locked"}
        assert c.get("/api/health").json()["vault"] == "locked"
        assert c.get("/api/ssh/key").status_code == 200  # the public half: still there
        assert c.post("/api/vault/entries", json={"name": "x", "secret": "y"}, headers=H).status_code == 423
        # An observer signing in doesn't unlock it; an admin does.
        signin(c, "obi", OBI)
        assert hub.vault.state == "locked"
        signin(c, "bea", BEA)
        assert hub.vault.state == "locked"  # bea has no slot yet
        signin(c, "ada", ADA)
        assert hub.vault.state == "unlocked"
        signin(c, "bea", BEA)  # signed in while unlocked: she gets her slot
        c.post("/api/vault/lock", json={}, headers=H)
        signin(c, "bea", BEA)
        assert hub.vault.state == "unlocked"
        url = f"/api/vault/rings/{KASTELLAN}/recovery"
        recovery = c.post(url, json={"password": BEA}, headers=H).json()["recovery"]
        c.post("/api/vault/lock", json={}, headers=H)
        old = {"recovery": made["recovery"][KASTELLAN], "ring": KASTELLAN}
        assert c.post("/api/vault/unlock", json=old, headers=H).status_code == 403
        new = {"recovery": recovery, "ring": KASTELLAN}
        assert c.post("/api/vault/unlock", json=new, headers=H).json()["opened"] == [KASTELLAN]
    log = audit.read_text()
    assert "router pw" not in log and "nas pw" not in log and ADA not in log
    assert '"vault.create"' in log and '"vault.reveal"' in log and '"vault.lock"' in log


def test_not_while_signin_is_off(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("devices:\n  box:\n    host: 127.0.0.1\n")
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        assert c.post("/api/vault/create", json={"password": "x"}, headers=H).status_code == 403


async def test_a_locked_key_ring_stops_the_router_poll(tmp_path: Path) -> None:
    """The router's login is in the key ring: locked, its poll says so instead of failing hard."""
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        hub = c.app.state.hub
        hub.vault.create("ada", ADA, password_tag(hub.config.users["ada"].password))
        hub.secrets.set("openwrt-box", "pw")
        hub.lock_vault()
        with pytest.raises(Locked):
            hub.ubus("box")


def test_the_cli_leaves_the_key_ring_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kastellan.__main__ import secret_command

    Vault(tmp_path / "vault.json").create("ada", ADA, "t")
    monkeypatch.setenv("KASTELLAN_DATA", str(tmp_path))
    with pytest.raises(SystemExit, match="key ring"):
        secret_command("set", "x")


def test_an_ssh_key_entry_imports(tmp_path: Path) -> None:
    key = asyncssh.generate_private_key("ssh-ed25519")
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    keys = Keys(tmp_path / "ssh", v)
    keys._store(key, "vpn")
    assert keys.hub_key("vpn").get_fingerprint() == key.get_fingerprint()
    assert v.entry("ssh/vpn").kind == "ssh-key"


async def test_a_push_while_locked_waits(tmp_path: Path) -> None:
    """Push's key is in the key ring: locked, a push fails softly (retried), never raises."""
    import httpx

    from kastellan.push import Push

    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    push = Push(tmp_path, v)
    push.vapid()  # made into the key ring
    sent: list[str] = []
    transport = httpx.MockTransport(lambda r: sent.append(str(r.url)) or httpx.Response(201))
    browser = {"endpoint": "https://fcm.googleapis.com/fcm/send/x", "keys": {}}
    v.lock()
    push.forget_key()
    async with httpx.AsyncClient(transport=transport) as client:
        assert await push._post(client, browser, {"title": "x"}) is False
    assert sent == []


def settings(level: str = "balanced", **rings) -> VaultSettings:
    return VaultSettings.model_validate({"level": level, "rings": rings})


def test_rings_open_by_themselves(tmp_path: Path) -> None:
    """A key file, an environment variable, a key in config.yaml: each opens its ring at start;
    a missing or wrong one leaves it locked, and says why."""
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.put("router", "pw")
    v.add_ring("site", "Site", "ada", ADA, "t")
    keyfile, env_key, config_key = tmp_path / "ring.key", new_ring_key(), new_ring_key()
    keyfile.write_text(new_ring_key() + "\n")
    keyfile.chmod(0o644)  # readable by others: opens, with a warning
    chosen = settings(
        kastellan={"unlock": ["password", {"keyfile": str(keyfile)}]},
        people={"unlock": ["password", {"env": "PEOPLE_KEY"}]},
        site={"unlock": ["config"], "key": config_key},
    )
    v.policy = lambda r: policy(chosen, r)
    v.set_ways(KASTELLAN, {("keyfile", str(keyfile)): keyfile.read_text().strip()})
    v.set_ways(PEOPLE, {("env", "PEOPLE_KEY"): env_key})
    v.set_ways("site", {("config", ""): config_key})
    v.lock()
    restarted = Vault(tmp_path / "vault.json")
    restarted.policy = v.policy
    opened = restarted.unlock_by_itself({"PEOPLE_KEY": env_key}, {"site": config_key})
    assert sorted(opened) == [KASTELLAN, PEOPLE, "site"] and restarted.get("router") == "pw"
    assert "read by others" in restarted.problems[KASTELLAN]
    wrong = Vault(tmp_path / "vault.json")
    wrong.policy = v.policy
    assert wrong.unlock_by_itself({"PEOPLE_KEY": new_ring_key()}, {}) == [KASTELLAN]
    assert "doesn't open it" in wrong.problems[PEOPLE] and "no key" in wrong.problems["site"]


def test_ring_policies(tmp_path: Path) -> None:
    """People's logins never serve background work; rings close as their policy says."""
    now = [0.0]
    v = Vault(tmp_path / "vault.json", clock=lambda: now[0])
    v.create("ada", ADA, "t")
    v.policy = lambda r: policy(settings("strict"), r)
    v.put("nas-root", "pw", ring=PEOPLE)
    v.put("router", "pw")
    with pytest.raises(NotForKastellan):
        v.get("nas-root")
    assert v.get("nas-root", by="ada") == "pw"
    now[0] = 14 * 60
    assert v.relock(admins_signed_in=True) == []
    v.get("nas-root", by="ada")  # used: idle from now
    now[0] = 30 * 60
    assert v.relock(admins_signed_in=True) == [PEOPLE]  # strict: idle 15 minutes
    assert v.is_open(KASTELLAN)  # stays until a restart or Lock
    v.policy = lambda r: policy(settings("balanced"), r)
    v.unlock("ada", ADA)
    assert v.relock(admins_signed_in=True) == []
    assert v.relock(admins_signed_in=False) == [PEOPLE]  # balanced: while an admin is signed in
    # People's logins never become Kastellan's to use, whatever config.yaml says.
    assert not policy(settings(people={"background": True}), PEOPLE).background


def test_moving_an_entry(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.put("nas-root", "pw", ring=PEOPLE)
    v.move("nas-root", KASTELLAN)
    assert v.entry("nas-root").ring == KASTELLAN and v.get("nas-root") == "pw"
    v.lock(PEOPLE)
    with pytest.raises(Locked):
        v.move("nas-root", PEOPLE)  # both rings must be open
    with pytest.raises(ValueError):
        v.remove_ring(PEOPLE)  # Kastellan's two stay


def test_the_levels() -> None:
    assert policy(settings("convenient"), KASTELLAN).unlock == (("password", ""), ("config", ""))
    assert policy(settings("balanced"), KASTELLAN).unlock == (("password", ""),)
    assert policy(settings("strict"), PEOPLE).stays == "idle:15"
    assert policy(settings("balanced"), "site").stays == "signed_in"
    own = policy(
        settings("strict", kastellan={"unlock": [{"env": "K"}], "stays_unlocked": "restart"}), KASTELLAN
    )
    assert own.unlock == (("env", "K"),) and own.stays == "lock"
    with pytest.raises(ValueError):
        settings(kastellan={"unlock": ["config"]})  # a key in config.yaml needs the key
    with pytest.raises(ValueError):
        settings(kastellan={"unlock": [{"keyfile": "relative"}]})


def test_the_level_from_the_keys_page(tmp_path: Path) -> None:
    """Choosing "convenient" writes a ring key into config.yaml (masked wherever it is shown),
    sets it up as a way to open the ring, and it opens by itself after a restart."""
    audit = tmp_path / "audit.log"
    path = config(tmp_path)
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        signin(c, "ada", ADA)
        c.post("/api/vault/create", json={"password": ADA}, headers=H)
        body = {"password": ADA, "level": "convenient"}
        assert c.put("/api/vault/settings", json=body | {"password": "x"}, headers=H).status_code == 403
        r = c.put("/api/vault/settings", json=body, headers=H)
        assert r.status_code == 200, r.text
        key = c.app.state.hub.config.vault.rings[KASTELLAN].key
        assert key and key in path.read_text()
        assert key not in json.dumps(c.get("/api/settings").json())
        ring = next(x for x in c.get("/api/vault").json()["rings"] if x["id"] == KASTELLAN)
        assert {"way": "config", "source": "", "ready": True, "users": []} in ring["ways"]
        assert c.post("/api/vault/rings", json={"id": "site", "password": ADA}, headers=H).json()["recovery"]
    assert key not in audit.read_text()
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:  # a restart
        hub = c.app.state.hub
        assert hub.vault.is_open(KASTELLAN) and not hub.vault.is_open(PEOPLE)
        assert hub.vault.state == "partly"


# --- The review's findings and the decisions after it ------------------------------------------


def test_a_locked_rings_slots_are_never_touched(tmp_path: Path) -> None:
    """A sign-in while a ring is locked never drops its password slots (they can't be made again
    without its key), and a ring never ends up with only its recovery key."""
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.lock(PEOPLE)
    chosen = settings(people={"unlock": [{"env": "PEOPLE_KEY"}]})  # changed while locked
    v.policy = lambda r: policy(chosen, r)
    v.keep_slot("ada", ADA, "t", {"ada"})
    v.keep_slot("ada", ADA, "t", set())  # also not when ada was briefly no admin
    v.policy = lambda r: policy(None, r)  # changed back
    v.lock()
    assert sorted(v.unlock("ada", ADA)) == [KASTELLAN, PEOPLE]
    # Open, with a policy that has no way but the recovery key left: the slots stay.
    nothing = settings(people={"unlock": []})
    v.policy = lambda r: policy(nothing, r)
    v.keep_slot("ada", ADA, "t", {"ada"})
    v.policy = lambda r: policy(None, r)
    v.lock()
    assert PEOPLE in v.unlock("ada", ADA)
    # A ring whose only ways are the ones being removed keeps them.
    v.keep_slot("ada", ADA, "t", set())  # ada no admin: her slot would go, the last but recovery
    assert PEOPLE in [r["id"] for r in v.rings() if any(s["way"] == "password" for s in r["slots"])]


def test_a_way_no_longer_chosen_opens_nothing(tmp_path: Path) -> None:
    """A key in config.yaml (also one left in a config backup) stops opening the ring once that
    way is no longer chosen and the ring is open again."""
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    key = new_ring_key()
    assert v.set_ways(KASTELLAN, {("config", ""): key})
    assert not v.set_ways(KASTELLAN, {("config", ""): key})  # the same: nothing written
    assert v.set_ways(KASTELLAN, {})
    v.lock(by_hand=False)
    chosen = settings(kastellan={"unlock": ["password", "config"], "key": key})
    v.policy = lambda r: policy(chosen, r)
    assert v.unlock_by_itself({}, {KASTELLAN: key}) == []
    assert "not set up" in v.problems[KASTELLAN]


def test_changed_ways_follow_a_config_reload(tmp_path: Path) -> None:
    path = config(tmp_path)
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        signin(c, "ada", ADA)
        c.post("/api/vault/create", json={"password": ADA}, headers=H)
        hub = c.app.state.hub
        key = new_ring_key()
        text = (
            path.read_text()
            + f"vault:\n  rings:\n    kastellan:\n      unlock: [password, config]\n      key: {key}\n"
        )
        version = c.get("/api/config").json()["version"]
        assert c.put("/api/config", json={"text": text, "version": version}, headers=H).status_code == 200
        ways = {s["way"] for r in hub.vault.rings() if r["id"] == KASTELLAN for s in r["slots"]}
        assert "config" in ways  # set up at the reload, the ring being open
        version = c.get("/api/config").json()["version"]
        back = path.read_text().split("vault:")[0]
        assert c.put("/api/config", json={"text": back, "version": version}, headers=H).status_code == 200
        ways = {s["way"] for r in hub.vault.rings() if r["id"] == KASTELLAN for s in r["slots"]}
        assert "config" not in ways


def test_kastellans_keys_stay_its_own_in_the_key_ring(tmp_path: Path) -> None:
    from kastellan.sshkeys import kastellan_keys

    keys = Keys(tmp_path / "ssh")
    for which in ("hub", "act", "vpn"):
        keys.hub_key(which)
    before = sorted(kastellan_keys(keys).values())
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "ada", ADA)
        c.post("/api/vault/create", json={"password": ADA}, headers=H)
        hub = c.app.state.hub
        assert sorted(kastellan_keys(hub.keys).values()) == before  # the files are gone, not the keys
        assert c.get("/api/ssh/keys").status_code == 200
        c.post("/api/vault/lock", json={}, headers=H)
        assert sorted(kastellan_keys(hub.keys).values()) == before  # also while locked


def test_a_logins_user_and_notes_are_sealed(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    v.put("nas", "pw", ring=PEOPLE, name="NAS", user="andreas", notes="PIN 4711")
    text = v.path.read_text()
    assert "andreas" not in text and "4711" not in text and "NAS" in text  # names stay visible
    assert (v.entry("nas").user, v.entry("nas").notes) == ("andreas", "PIN 4711")
    v.move("nas", KASTELLAN)
    assert v.entry("nas").user == "andreas"
    v.lock()
    assert (v.entry("nas").user, v.entry("nas").notes) == ("", "")


def test_only_signed_in_admins_see_the_key_ring(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("devices:\n  box:\n    host: 127.0.0.1\n")
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        assert c.get("/api/vault").status_code == 403  # sign-in off: nobody


def test_the_files_go_only_once_everything_is_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that can't be removed is a note: the rings stay (they hold the secret now)."""
    from kastellan import vault_api

    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "openwrt-box").write_text("router pw")

    def stuck(path: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vault_api, "shred", stuck)
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "ada", ADA)
        made = c.post("/api/vault/create", json={"password": ADA}, headers=H).json()
        assert made["notes"] and "remove it by hand" in made["notes"][0]
        assert c.app.state.hub.secrets.get("openwrt-box") == "router pw"
        assert (tmp_path / "vault.json").exists()


def test_shredding_a_link_leaves_its_target(tmp_path: Path) -> None:
    from kastellan.vault import shred

    target, link = tmp_path / "mine", tmp_path / "link"
    target.write_text("keep me")
    link.symlink_to(target)
    shred(link)
    assert not link.exists() and target.read_text() == "keep me"
    plain = tmp_path / "plain"
    plain.write_text("secret")
    shred(plain)
    assert not plain.exists()


def test_a_damaged_key_ring_never_stops_a_sign_in(tmp_path: Path) -> None:
    path = config(tmp_path)
    (tmp_path / "vault.json").write_text("{not json")
    with TestClient(create_app(Settings(config=path, data=tmp_path))) as c:
        signin(c, "ada", ADA)
        assert c.get("/api/health").json()["vault"] == "damaged"
        state = c.get("/api/vault").json()
        assert state["state"] == "damaged" and "can't be read" in state["damaged"]


def test_a_persons_login_is_refused_as_such() -> None:
    assert not issubclass(NotForKastellan, OSError)  # never "can't reach the host"


def test_a_changed_password_says_how_to_open(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    recovery = v.create("ada", ADA, "t1")
    v.lock()
    assert v.unlock("ada", "new") == []  # her only slot opens with the old password
    assert sorted(v.note_stale("ada", "t2")) == [KASTELLAN, PEOPLE]
    assert "recovery key" in v.problems[PEOPLE]
    v.unlock_recovery(PEOPLE, recovery[PEOPLE])
    v.keep_slot("ada", "new", "t2", {"ada"})  # open now: the slot follows the new password
    v.lock()
    assert v.unlock("ada", "new") == [PEOPLE]
    # The old password opens people's ring no more; Kastellan's stayed locked, so its slot is
    # renewed once that ring is open.
    assert v.unlock("ada", ADA) == [KASTELLAN]


def test_public_halves_are_checked_on_opening(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    keys = Keys(tmp_path / "ssh", v)
    real = keys.public_key("act")
    doc = json.loads(v.path.read_text())
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode().strip()
    doc["rings"][KASTELLAN]["entries"]["ssh/act"]["public"] = other  # written by hand
    v.path.write_text(json.dumps(doc))
    v.lock()
    v.unlock("ada", ADA)
    assert keys.public_key("act") == real and "didn't match" in v.problems[KASTELLAN]


def test_using_the_app_keeps_a_ring_open_not_a_page_left_open(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "ada", ADA)
        hub = c.app.state.hub
        assert "ada" in hub.sessions.active(60)  # signing in counts (the session made first)
        hub.sessions._active["ada"] = 0
        c.get("/api/devices", headers={"X-Kastellan-Auto": "1"})  # a page refreshing itself
        assert "ada" not in hub.sessions.active(60)
        c.get("/api/devices")
        assert "ada" in hub.sessions.active(60)


def test_a_secret_is_shown_only_through_ones_own_slot(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    assert v.opens_with(PEOPLE, "ada", ADA) and not v.opens_with(PEOPLE, "ada", "x")
    assert not v.opens_with(PEOPLE, "bea", BEA)  # open, but not hers


def test_locked_by_hand_stays_locked_through_reloads(tmp_path: Path) -> None:
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    key = new_ring_key()
    v.set_ways(KASTELLAN, {("config", ""): key})
    chosen = settings(kastellan={"unlock": ["password", "config"], "key": key})
    v.policy = lambda r: policy(chosen, r)
    v.lock(KASTELLAN)  # by hand
    assert v.unlock_by_itself({}, {KASTELLAN: key}) == []  # a reload: stays locked
    v.unlock("ada", ADA)
    v.lock(KASTELLAN, by_hand=False)  # closed by its policy, not by hand
    assert v.unlock_by_itself({}, {KASTELLAN: key}) == [KASTELLAN]


def test_a_way_whose_key_is_missing_now_keeps_its_slot(tmp_path: Path) -> None:
    """The environment variable not set at this moment (opened by a password today): its slot
    stays, so the ring opens by itself again once the variable is back."""
    v = Vault(tmp_path / "vault.json")
    v.create("ada", ADA, "t")
    key = new_ring_key()
    v.set_ways(KASTELLAN, {("env", "RING"): key})
    assert not v.set_ways(KASTELLAN, {}, frozenset({("env", "RING")}))
    v.lock(by_hand=False)
    chosen = settings(kastellan={"unlock": ["password", {"env": "RING"}]})
    v.policy = lambda r: policy(chosen, r)
    assert v.unlock_by_itself({"RING": key}, {}) == [KASTELLAN]


def test_where_a_login_may_go_needs_the_password(tmp_path: Path) -> None:
    """A login's devices decide which hosts a terminal sends it to: changing them needs the
    admin's password typed again; only real devices; sealed with the login."""
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "ada", ADA)
        c.post("/api/vault/create", json={"password": ADA}, headers=H)
        form = {"name": "Box root", "ring": PEOPLE, "user": "root", "secret": "box pw"}
        assert c.post("/api/vault/entries", json=form, headers=H).status_code == 200
        url = "/api/vault/entries/box-root"
        assert c.put(url, json=form | {"secret": None, "devices": ["box"]}, headers=H).status_code == 403
        bad = form | {"secret": None, "devices": ["nope"], "password": ADA}
        assert c.put(url, json=bad, headers=H).status_code == 400
        ok = form | {"secret": None, "devices": ["box"], "password": ADA}
        assert c.put(url, json=ok, headers=H).status_code == 200
        hub = c.app.state.hub
        assert hub.vault.entry("box-root").devices == ["box"]
        # A later change of its name keeps where it may go.
        assert c.put(url, json=form | {"secret": None, "name": "Box"}, headers=H).status_code == 200
        assert hub.vault.entry("box-root").devices == ["box"]
        assert '"devices"' not in (tmp_path / "vault.json").read_text()  # sealed with user and notes


def test_moving_into_a_locked_ring_changes_nothing(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(config=config(tmp_path), data=tmp_path))) as c:
        signin(c, "ada", ADA)
        c.post("/api/vault/create", json={"password": ADA}, headers=H)
        form = {"name": "Box root", "ring": PEOPLE, "user": "root", "secret": "box pw"}
        assert c.post("/api/vault/entries", json=form, headers=H).status_code == 200
        hub = c.app.state.hub
        hub.vault.lock(KASTELLAN)
        moved = form | {"ring": KASTELLAN, "secret": "new pw", "name": "Box"}
        assert c.put("/api/vault/entries/box-root", json=moved, headers=H).status_code == 423
        entry = hub.vault.entry("box-root")
        assert entry.ring == PEOPLE and entry.name == "Box root"
        assert hub.vault.get("box-root", by="ada") == "box pw"


def test_kastellans_own_secrets_are_never_shown_or_edited() -> None:
    """Every name in secrets.OWN (the fingerprint salt too) is Kastellan's own on the Keys page."""
    from kastellan import vault_api
    from kastellan.secrets import FINGERPRINT_SALT, OWN

    assert FINGERPRINT_SALT in OWN
    assert all(vault_api.own(name) for name in OWN)
    assert vault_api.own("ssh/hub") and not vault_api.own("wg-easy")
