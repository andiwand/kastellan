"""The key ring over HTTP (vault.py): its state and entries (never their secrets unless revealed
on purpose, with the password typed again), making it (which takes in the secrets kept as files
so far), unlocking, locking. Admins only; every change needs sign-in and is audited."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import asyncssh
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import config_edit
from .auth import password_tag, verify_password
from .auth_api import Who, client_address, require_signin
from .groups_api import SALT_SECRET
from .push import VAULT_ID as PUSH_ID
from .ssh import Keys
from .vault import (
    AUTO,
    DEFAULT_RINGS,
    ID,
    LEVELS,
    PEOPLE,
    Locked,
    WrongKey,
    new_ring_key,
    shred,
    ways_of,
)

if TYPE_CHECKING:
    from .hub import Hub


class Password(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class Unlock(BaseModel):
    password: str | None = Field(default=None, max_length=1024)
    recovery: str | None = Field(default=None, max_length=200)
    ring: str | None = Field(default=None, max_length=40)  # the recovery key's ring


class LockBody(BaseModel):
    ring: str | None = Field(default=None, max_length=40)  # None: all


class NewRing(Password):
    id: str = Field(max_length=40)
    name: str = Field(default="", max_length=100)


class RingChoice(BaseModel):
    # None: what the level says. Items: "password", "config", {"keyfile": path}, {"env": NAME}.
    unlock: list[str | dict[str, str]] | None = None
    stays_unlocked: str | None = None
    background: bool | None = None
    make_keyfile: bool = False  # write a new key to the key file named (if it isn't there)


class KeySettings(Password):
    level: Literal["convenient", "balanced", "strict"] = "balanced"
    rings: dict[str, RingChoice] = Field(default_factory=dict)


class EntryForm(BaseModel):
    id: str | None = Field(default=None, max_length=128)  # new: made from the name when left out
    ring: str = Field(default=PEOPLE, max_length=40)
    kind: Literal["password", "token", "ssh-key", "other"] = "password"
    name: str = Field(default="", max_length=200)
    user: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    secret: str | None = Field(default=None, max_length=16384)  # None on a change: kept
    # A login's devices (a terminal sends it only there); None on a change: kept. Changing them
    # needs the admin's password typed again: it decides where a password may go.
    devices: list[str] | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=1024)


def own(id: str) -> bool:
    """Kastellan's own keys: made and replaced by Kastellan (rotation), never edited here."""
    return id.startswith("ssh/") or id in (PUSH_ID, "device-backups", "syslog-ca-key", "hub-backup-key")


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/vault")

    def admin_signed_in(request: Request) -> Who:
        w = require_signin(request)  # refuses while sign-in is off: a key ring needs a password
        if not w.is_admin:
            raise HTTPException(403, "admins only")
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; it has no key ring")
        return w

    async def check_password(w: Who, password: str) -> None:
        user = hub.config.users.get(w.user or "")
        if user is None or not await asyncio.to_thread(verify_password, password, user.password):
            raise HTTPException(403, "wrong password")

    def audit(what: str, w: Who, request: Request, **details: Any) -> None:
        hub.audit.record(f"vault.{what}", w.user, client_address(request.scope), **details)

    def used_by(id: str) -> list[str]:
        """Where an entry is used: Kastellan's own needs, and config.yaml naming it."""
        out = []
        labels = {
            "ssh/hub": "Kastellan's SSH key",
            "ssh/act": "the actor's key",
            "ssh/vpn": "the VPN scripts' key",
        }
        if id.split(".")[0] in labels:
            out.append(labels[id.split(".")[0]] + (" (rotation)" if "." in id else ""))
        if id == PUSH_ID:
            out.append("push notifications")
        if id == SALT_SECRET:
            out.append("groups: Wi-Fi password fingerprints")
        if id == "syslog-ca-key":
            out.append("syslog certificate authority")
        if id == "hub-backup-key":
            out.append("hub backup encryption")
        if id == "device-backups":
            out.append("device backup encryption")
        for dev, device in hub.config.devices.items():
            if device.openwrt is not None and hub.openwrt_secret(dev) == id:
                out.append(f"{dev}: its OpenWrt login")
            if device.fritzbox is not None and hub.fritz_secret(dev) == id:
                out.append(f"{dev}: its FRITZ!Box login")
            if device.fritzbox is not None and id == f"fritzbox-export-{dev}":
                out.append(f"{dev}: its backup export password")
        for vid, vpn in hub.config.vpns.items():
            if vpn.kind == "wireguard" and vpn.password_secret == id:
                out.append(f"{hub.config.vpn_name(vid)}: its wg-easy login")
        return out

    def ring_view(ring: dict[str, Any]) -> dict[str, Any]:
        rid = ring["id"]
        p = hub.vault.policy(rid)
        slots = ring.pop("slots")
        ways = []
        for way, source in p.unlock:
            if way == "password":
                users = sorted({s["user"] for s in slots if s["way"] == "password"})
                ways.append({"way": way, "source": "", "ready": bool(users), "users": users})
            else:
                ready = any(s["way"] == way and s["source"] == source for s in slots)
                ways.append({"way": way, "source": source, "ready": ready, "users": []})
        return ring | {
            "ways": ways,
            "recovery": any(s["way"] == "recovery" for s in slots),
            "stays_unlocked": p.stays,
            "background": p.background,
            "problem": hub.vault.problems.get(rid),
            "builtin": rid in DEFAULT_RINGS,
        }

    @api.get("")
    def state(request: Request, response: Response) -> dict[str, Any]:
        # Signed-in admins only (also while sign-in is off: a key ring needs a password).
        w = require_signin(request)
        if not w.is_admin:
            raise HTTPException(403, "admins only")
        response.headers["Cache-Control"] = "no-store"
        out: dict[str, Any] = {
            "state": hub.vault.state,
            "level": hub.config.vault.level,
            "damaged": hub.vault.damaged,
            "rings": [],
            "entries": [],
            "files": hub.secrets.names() if not hub.vault.exists else [],  # to be taken in
        }
        if out["damaged"] is None:
            out["rings"] = [ring_view(r) for r in hub.vault.rings()]
            out["entries"] = [
                e.view() | {"used_by": used_by(e.id), "own": own(e.id)} for e in hub.vault.entries()
            ]
        return out

    @api.post("/create")
    async def create(body: Password, request: Request) -> dict[str, Any]:
        """Make the two key rings (Kastellan's, people's logins), opened by this admin's
        password, and take in what is kept as plain files so far (secrets, Kastellan's keys that
        can change things, push's key) into Kastellan's: each one checked to read back from the
        ring before its file is overwritten and removed."""
        w = admin_signed_in(request)
        await check_password(w, body.password)
        if hub.vault.exists:
            raise HTTPException(409, "there is a key ring already")
        user = hub.config.users[w.user or ""]
        recovery = await asyncio.to_thread(
            hub.vault.create, w.user or "", body.password, password_tag(user.password)
        )
        try:
            taken, gone = migrate(hub)
        except Exception as e:  # nothing half done: no file was touched, the new key rings go
            hub.lock_vault(by_hand=False)
            hub.vault.path.unlink(missing_ok=True)
            raise HTTPException(500, f"taking in the secrets failed, nothing changed: {e}") from e
        # Everything read back from the rings: from here on the rings stay, whatever happens to
        # the plain files (a file that can't be removed is a note, never a reason to undo).
        notes = []
        for path in gone:
            try:
                shred(path)
            except OSError as e:
                notes.append(f"couldn't remove {path}: {e.strerror or type(e).__name__}; remove it by hand")
        hub.sync_slots()
        audit("create", w, request, taken=taken, rings=list(recovery), notes=notes)
        return {"recovery": recovery, "taken": taken, "notes": notes}

    @api.post("/unlock")
    async def unlock(body: Unlock, request: Request) -> dict[str, Any]:
        """Signing in opens the rings an admin's password opens; this is for when that couldn't
        (a ring's recovery key after a password change), or after Lock."""
        w = admin_signed_in(request)
        try:
            if body.recovery:
                if not body.ring:
                    raise HTTPException(400, "which ring the recovery key is for")
                await asyncio.to_thread(hub.vault.unlock_recovery, body.ring, body.recovery)
                opened = [body.ring]
            elif body.password:
                await check_password(w, body.password)
                opened = await asyncio.to_thread(hub.vault.unlock, w.user or "", body.password)
            else:
                raise HTTPException(400, "your password or a recovery key")
        except WrongKey as e:
            audit("unlock", w, request, outcome="refused", ring=body.ring)
            raise HTTPException(403, str(e)) from e
        if body.password:  # their slots made again if their password changed since
            await hub.keep_slot(w.user or "", body.password)
        hub.sync_slots()  # ways chosen while it was locked are set now
        audit(
            "unlock",
            w,
            request,
            outcome="done",
            rings=opened,
            by="recovery key" if body.recovery else "password",
        )
        return {"state": hub.vault.state, "opened": opened}

    @api.post("/lock")
    async def lock(body: LockBody, request: Request) -> dict[str, Any]:
        w = admin_signed_in(request)
        closed = hub.lock_vault(body.ring, by_hand=True)
        audit("lock", w, request, rings=closed)
        return {"state": hub.vault.state}

    @api.post("/rings")
    async def add_ring(body: NewRing, request: Request) -> dict[str, str]:
        """Another ring (e.g. a site's logins), opened by this admin's password; its recovery
        key, shown once."""
        w = admin_signed_in(request)
        await check_password(w, body.password)
        if not hub.vault.exists:
            raise HTTPException(409, "make the key ring first")
        user = hub.config.users[w.user or ""]
        try:
            recovery = await asyncio.to_thread(
                hub.vault.add_ring, body.id, body.name.strip() or body.id, w.user or "", body.password,
                password_tag(user.password),
            )  # fmt: skip
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit("ring.add", w, request, ring=body.id)
        return {"recovery": recovery}

    @api.delete("/rings/{rid}")
    async def remove_ring(rid: str, request: Request) -> dict[str, str]:
        w = admin_signed_in(request)
        try:
            hub.vault.remove_ring(rid)
        except KeyError as e:
            raise HTTPException(404, "no such ring") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        audit("ring.remove", w, request, ring=rid)
        return {"ring": rid}

    @api.post("/rings/{rid}/recovery")
    async def recovery(rid: str, body: Password, request: Request) -> dict[str, str]:
        """A new recovery key for one ring (the old one stops working), shown once."""
        w = admin_signed_in(request)
        await check_password(w, body.password)
        if rid not in hub.vault.ring_ids():
            raise HTTPException(404, "no such ring")
        key = await asyncio.to_thread(hub.vault.new_recovery, rid)
        audit("recovery", w, request, ring=rid)
        return {"recovery": key}

    @api.put("/settings")
    async def settings(body: KeySettings, request: Request) -> dict[str, Any]:
        """How secure the rings are: the level, and each ring's ways to open, how long it stays
        open, whether Kastellan may use it by itself. Written to config.yaml (`vault:`); each
        open ring's slots then match its ways (a key in config.yaml made here when chosen, a key
        file written only when asked to). A ring that is locked gets its new ways once open."""
        w = admin_signed_in(request)
        await check_password(w, body.password)
        if not hub.vault.exists:
            raise HTTPException(409, "make the key ring first")
        known = set(hub.vault.ring_ids())
        old_rings = hub.config.vault.rings
        rings: dict[str, Any] = {}
        notes: list[str] = []
        unknown = set(body.rings) - known
        if unknown:
            raise HTTPException(400, f"no ring {sorted(unknown)[0]!r}")
        for rid in sorted(known):
            choice = body.rings.get(rid) or RingChoice()
            entry: dict[str, Any] = {}
            if choice.unlock is not None:
                entry["unlock"] = choice.unlock
            if choice.stays_unlocked:
                entry["stays_unlocked"] = choice.stays_unlocked
            if choice.background is not None and rid != PEOPLE:
                entry["background"] = choice.background
            ways = (
                ways_of(choice.unlock)
                if choice.unlock is not None
                else LEVELS[body.level].get(rid, LEVELS[body.level]["*"]).unlock
            )
            if ("config", "") in ways:
                old = old_rings.get(rid)
                entry["key"] = old.key if old is not None and old.key else new_ring_key()
            for way, source in ways:
                if way == "keyfile" and choice.make_keyfile:
                    try:  # never over a file that is there: that may be the key already in use
                        make_keyfile(source)
                        notes.append(f"{rid}: wrote a new key to {source}")
                    except FileExistsError:
                        pass
            if entry:
                rings[rid] = entry
        old_text = config_edit.read(hub.settings.config)
        value = {"level": body.level, **({"rings": rings} if rings else {})}
        new_text = config_edit.set_top(old_text, "vault", value)
        from .hub import ConfigProblem

        try:
            hub.check(new_text)
            hub.save_config(old_text, new_text)
        except ConfigProblem as e:
            raise HTTPException(400, e.message) from e
        except OSError as e:
            raise HTTPException(500, f"can't write config.yaml: {e.strerror or type(e).__name__}") from e
        hub.audit.record(
            "config.vault",
            w.user,
            client_address(request.scope),
            changes=config_edit.diff(old_text, new_text),
        )
        notes += hub.sync_slots()
        for rid in hub.vault.ring_ids():
            p = hub.vault.policy(rid)
            if not p.background and any(way in AUTO for way, _ in p.unlock):
                notes.append(
                    f"{rid}: opens by itself at start now — people's logins are then readable by "
                    "anyone with that key, while Kastellan still never uses them by itself"
                )
        audit("settings", w, request, level=body.level, notes=notes)
        return {"notes": notes, "state": hub.vault.state}

    def form_of(body: EntryForm, secret: str | None) -> dict[str, Any]:
        return {"kind": body.kind, "name": body.name.strip(), "user": body.user.strip(),
                "notes": body.notes, "secret": secret, "devices": body.devices}  # fmt: skip

    async def check_devices(w: Who, body: EntryForm, before: list[str]) -> None:
        """New devices for a login: real ones, and the admin's password typed again (with their
        own way into its ring)."""
        if body.devices is None or sorted(set(body.devices)) == sorted(before):
            return
        unknown = [d for d in body.devices if d not in hub.config.devices]
        if unknown:
            raise HTTPException(400, f"no such device: {', '.join(unknown)}")
        if not body.password:
            raise HTTPException(403, "type your password again to change where a login may go")
        await check_password(w, body.password)
        if not await asyncio.to_thread(hub.vault.opens_with, body.ring, w.user or "", body.password):
            raise HTTPException(403, "your password doesn't open this ring (sign in again to get a slot)")

    @api.post("/entries")
    async def add(body: EntryForm, request: Request) -> dict[str, str]:
        w = admin_signed_in(request)
        id = body.id or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:64]
        if not id or not ID.match(id) or own(id):
            raise HTTPException(400, "give it a name (letters or digits)")
        if hub.vault.has(id):
            raise HTTPException(409, f"there is an entry {id!r} already")
        if body.ring not in hub.vault.ring_ids():
            raise HTTPException(400, f"no ring {body.ring!r}")
        if not body.secret:
            raise HTTPException(400, "the secret is missing")
        await check_devices(w, body, [])
        f = form_of(body, body.secret)
        try:
            hub.vault.put(id, f.pop("secret"), ring=body.ring, **f)
        except Locked as e:
            raise HTTPException(423, str(e)) from e
        audit("add", w, request, entry=id, ring=body.ring, kind=body.kind)
        return {"id": id}

    @api.put("/entries/{id:path}")
    async def change(id: str, body: EntryForm, request: Request) -> dict[str, str]:
        """Its name, notes, secret; and its ring (moved: sealed again with that ring's key)."""
        w = admin_signed_in(request)
        old = hub.vault.entry(id)
        if old is None:
            raise HTTPException(404, "no such entry")
        if own(id):
            raise HTTPException(400, "Kastellan's own keys change by rotating them")
        if body.ring != old.ring:
            if body.ring not in hub.vault.ring_ids():
                raise HTTPException(400, f"no ring {body.ring!r}")
            if used_by(id) and not hub.vault.policy(body.ring).background:
                raise HTTPException(400, "Kastellan uses it by itself: it stays in a ring Kastellan may use")
        await check_devices(w, body, old.devices)
        f = form_of(body, body.secret or None)
        try:
            hub.vault.put(id, f.pop("secret"), **f)
            if body.ring != old.ring:
                hub.vault.move(id, body.ring)
        except Locked as e:
            raise HTTPException(423, str(e)) from e
        audit(
            "change",
            w,
            request,
            entry=id,
            secret_changed=bool(body.secret),
            ring=body.ring,
            **({"devices": sorted(set(body.devices))} if body.devices is not None else {}),
        )
        return {"id": id}

    @api.delete("/entries/{id:path}")
    async def delete(id: str, request: Request) -> dict[str, str]:
        w = admin_signed_in(request)
        if own(id) or used_by(id):
            raise HTTPException(400, f"in use: {', '.join(used_by(id)) or 'by Kastellan'}")
        try:
            hub.vault.delete(id)
        except KeyError as e:
            raise HTTPException(404, "no such entry") from e
        except Locked as e:
            raise HTTPException(423, str(e)) from e
        audit("delete", w, request, entry=id)
        return {"id": id}

    @api.post("/entries/{id:path}/reveal")
    async def reveal(id: str, body: Password, request: Request, response: Response) -> dict[str, str]:
        """One secret, shown to the admin who asks with their password typed again (an SSH key's
        private half never: only Kastellan uses it)."""
        w = admin_signed_in(request)
        await check_password(w, body.password)
        entry = hub.vault.entry(id)
        if entry is None:
            raise HTTPException(404, "no such entry")
        if entry.kind == "ssh-key" or own(id):
            raise HTTPException(400, "a private key isn't shown")
        # Your own way into this ring, not only an open ring: an admin without a slot of their
        # own there (or with an outdated one) doesn't read its secrets.
        if not await asyncio.to_thread(hub.vault.opens_with, entry.ring, w.user or "", body.password):
            audit("reveal", w, request, entry=id, outcome="refused")
            raise HTTPException(403, "your password doesn't open this ring (sign in again to get a slot)")
        response.headers["Cache-Control"] = "no-store"
        try:
            secret = hub.vault.get(id, by=w.user)
        except Locked as e:
            raise HTTPException(423, str(e)) from e
        audit("reveal", w, request, entry=id)
        return {"secret": secret}

    return api


def make_keyfile(path: str) -> None:
    """A new ring key in a file the admin named (0600, never over an existing file)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(new_ring_key() + "\n")


def migrate(hub: Hub) -> tuple[list[str], list[Path]]:
    """Take the plain files into the (new, unlocked) key ring; each read back from it. Returns
    what was taken (names only) and the files to remove now (the caller does, once all is in)."""
    taken: list[str] = []
    gone: list[Path] = []
    folder = hub.secrets.folder
    for name in hub.secrets.names():
        value = (folder / name).read_text().rstrip("\n")
        hub.vault.put(name, value, name=name)
        if hub.vault.get(name) != value:
            raise ValueError(f"{name} doesn't read back")
        taken.append(name)
        gone.append(folder / name)
    keys = hub.keys
    for of in Keys.IN_VAULT:
        for side in ("", "next", "old"):
            path = keys._file(of, side)
            if not path.exists():
                continue
            key = asyncssh.read_private_key(str(path))
            keys._store(key, of, side)  # the key ring exists now: into it
            back = keys._load(of, side)
            if back is None or back.get_fingerprint() != key.get_fingerprint():
                raise ValueError(f"the {of} key doesn't read back")
            taken.append(Keys.vault_id(of, side))
            gone += [path, path.with_suffix(".pub")] if not side else [path]
    pem = hub.push.folder / "vapid.pem"
    if pem.exists():
        text = pem.read_text()
        hub.vault.put(PUSH_ID, text, kind="other", name="Push key")
        if hub.vault.get(PUSH_ID) != text:
            raise ValueError("push's key doesn't read back")
        taken.append(PUSH_ID)
        gone.append(pem)
        hub.push.forget_key()
    return taken, gone
