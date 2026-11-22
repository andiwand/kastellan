"""Changing config.yaml from the app (config_edit.py): the whole file, and one device at a time.

Only with sign-in on, and only admins: config.yaml holds the users, and the VPN and SSH
settings; while sign-in is off anyone who reaches the hub would be an admin. Every change goes
to the audit log with what changed (password hashes masked).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal, Self
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import config_edit
from .auth import hash_password
from .auth_api import Who, client_address, require_signin
from .config import (
    ID,
    AlarmSettings,
    BackupCheck,
    Check,
    Config,
    DeviceBackupSettings,
    GroupSettings,
    HubBackupSettings,
    InternetSettings,
    Kind,
    LogSettings,
    MaintenanceWindow,
    NetworkSettings,
    NotifyChannel,
    OpenVpnSource,
    Role,
    VulnSettings,
    WifiSettings,
    WireGuardSource,
)
from .hub_errors import ConfigProblem, describe, yaml_problem
from .secrets import OWN

if TYPE_CHECKING:
    from .hub import Hub


class Powered(BaseModel):
    devices: list[str] = Field(max_length=500)  # every device this UPS powers, after the change
    version: str


class FileBody(BaseModel):
    text: str = Field(max_length=1_000_000)
    version: str | None = None  # from GET; None only when checking


class CheckForm(BaseModel):
    type: Literal["ping", "tcp", "http", "dns", "dhcp"]
    target: str | int | None = None
    name: str = Field(default="", max_length=80)
    # http: a status (100 to 599); dns: the addresses that must be among the answers.
    expect: int | list[str] | None = Field(default=None)
    contains: str | None = Field(default=None, min_length=1, max_length=200)
    server: str | None = Field(default=None, max_length=45)  # dns: the resolver to ask

    @model_validator(mode="after")
    def _status(self) -> Self:
        if isinstance(self.expect, int) and not 100 <= self.expect <= 599:
            raise ValueError("expect: a status from 100 to 599")
        return self

    verify: bool = False
    tls: bool | None = None
    interval: float | None = Field(default=None, ge=5, le=86400)
    timeout: float | None = Field(default=None, gt=0, le=60)
    exposed: bool = False


class RunsOnForm(BaseModel):
    host: str = Field(min_length=1, max_length=64)
    vm: str = Field(min_length=1, max_length=128)


class UpsForm(BaseModel):
    via: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=64)


class DeviceForm(BaseModel):
    name: str = Field(default="", max_length=120)
    host: str = Field(default="", max_length=253)  # empty only for a UPS read through its host
    kind: Kind = Kind.OTHER
    site: str | None = Field(default=None, max_length=40)
    runs_on: RunsOnForm | None = None  # a virtual machine on another device
    ups: UpsForm | None = None  # a UPS: which host reads it
    powered_by: str | None = Field(default=None, max_length=64)  # the UPS it hangs on
    tags: list[str] = Field(default_factory=list, max_length=30)
    always_on: bool | None = None
    exposed: bool = False
    checks: list[CheckForm] = Field(default_factory=list, max_length=50)
    version: str | None = None  # from the form: saving refuses if the file changed since


class NewDevice(DeviceForm):
    id: str = Field(min_length=1, max_length=64)


KEEP = "__keep__"  # in a form: "leave this secret as it is" (it is never sent to the page)

# The forms on the Config page, Groups and VPN: each a part of config.yaml, by top-level keys.
SECTIONS: dict[str, tuple[str, ...]] = {
    "general": ("check_interval", "retention_days", "heartbeat", "check_releases"),
    "alarms": ("alarms",),
    "notify": ("notify",),
    "vulns": ("vulns",),
    "networks": ("networks",),
    "groups": ("groups",),
    "sites": ("sites",),
    "users": ("users",),
    "maintenance": ("maintenance",),
    "backups": ("backups",),
    "logs": ("logs",),
    "device_backups": ("device_backups",),
    "internet": ("internet",),
    "wifi": ("wifi",),
    "hub_backup": ("hub_backup",),
}


class SecretBody(BaseModel):
    value: str = Field(min_length=1, max_length=4096)


class VpnBody(BaseModel):
    vpn: dict[str, Any]  # kind, name and that kind's settings
    new: bool = False  # adding: refused if the id is taken
    version: str  # from GET /api/settings or /api/config


def wg_secrets(config) -> set[str]:
    """The secret names VPN forms may set: each wg-easy login's password."""
    return {"wg-easy"} | {v.password_secret for v in config.vpns.values() if v.kind == "wireguard"}


class SectionBody(BaseModel):
    value: dict[str, Any]  # the section's top-level keys and their values
    version: str  # from GET /api/settings: saving over a change made since is refused


def _hide_password(url: str) -> str:
    """smtps://user:secret@host → smtps://user:__keep__@host (the page never gets it)."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{parts.username or ''}:{KEEP}@{host}"))


def _restore_password(url: str, old: str) -> str:
    """The password from `old` back into `url`, if the server and user stayed the same."""
    new, was = urlsplit(url), urlsplit(old)
    if (new.username, new.hostname, new.port) != (was.username, was.hostname, was.port):
        raise HTTPException(400, "the mail server changed: enter its password again")
    host = new.netloc.rsplit("@", 1)[1]
    return urlunsplit(new._replace(netloc=f"{new.username or ''}:{was.password or ''}@{host}"))


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    def guard(request: Request) -> Who:
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; its config can't be changed")
        return w

    def save(request: Request, w: Who, old: str, new: str, what: str, **details: Any) -> str:
        try:
            config = hub.check(new)
        except ConfigProblem as e:
            raise HTTPException(400, e.message) from e
        # Nobody saves themselves out of being an admin (they'd need the file to undo it).
        me = config.users.get(w.user or "")
        if me is None or me.role is not Role.ADMIN:
            raise HTTPException(400, f"this would take your own admin access ({w.user}); not saved")
        if new == old:
            return config_edit.version_of(old)
        try:
            hub.save_config(old, new)
        except OSError as e:
            raise HTTPException(500, f"can't write config.yaml: {e.strerror or type(e).__name__}") from e
        hub.audit.record(
            "config." + what,
            w.user,
            client_address(request.scope),
            changes=config_edit.diff(old, new),
            **details,
        )
        return config_edit.version_of(new)

    def current() -> str:
        try:
            return config_edit.read(hub.settings.config)
        except OSError as e:
            raise HTTPException(500, f"can't read config.yaml: {e.strerror or type(e).__name__}") from e

    @api.get("/config")
    def get_file(request: Request, response: Response) -> dict[str, Any]:
        guard(request)
        # It holds the users' password hashes and tokens: not kept by the browser.
        response.headers["Cache-Control"] = "no-store"
        text = current()
        return {"text": text, "version": config_edit.version_of(text), "error": hub.config_error}

    @api.post("/config/check")
    def check_file(body: FileBody, request: Request) -> dict[str, Any]:
        guard(request)
        try:
            hub.check(body.text)
        except ConfigProblem as e:
            return {"ok": False, "error": e.message}
        return {"ok": True, "error": None}

    # Saving reloads the hub (it starts and stops checks): these run on the event loop.
    @api.put("/config")
    async def put_file(body: FileBody, request: Request) -> dict[str, Any]:
        w = guard(request)
        old = current()
        if body.version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since you opened it; reload, then change it again")
        return {"version": save(request, w, old, body.text, "file")}

    def form_fields(form: DeviceForm) -> dict[str, Any]:
        tags = [t.strip() for t in form.tags if t.strip()]
        if any(not re.match(r"^[\w.-]{1,40}$", t) for t in tags):
            raise HTTPException(400, "tags are letters, digits, . _ - (up to 40)")
        return {
            "name": form.name.strip(),
            "host": form.host.strip(),
            "kind": form.kind.value,
            "site": (form.site or "").strip() or None,
            "runs_on": (
                {"host": form.runs_on.host.strip(), "vm": form.runs_on.vm.strip()} if form.runs_on else None
            ),
            "ups": (
                {"via": form.ups.via.strip(), "name": (form.ups.name or "").strip() or None}
                if form.ups and form.kind is Kind.UPS
                else None
            ),
            "powered_by": (form.powered_by or "").strip() or None,
            "tags": tags,
            "always_on": form.always_on,
            "exposed": form.exposed,
            "checks": [c.model_dump() for c in form.checks],
        }

    def edit(
        request: Request,
        id: str,
        fields: dict[str, Any] | None,
        new: bool,
        what: str,
        version: str | None,
    ) -> dict[str, Any]:
        w = guard(request)
        old = current()
        if version is not None and version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since the form was opened; open it again")
        try:
            text = config_edit.edit_device(old, id, fields, new)
        except YAMLError as e:
            raise HTTPException(400, f"config.yaml can't be read: {yaml_problem(e)}") from e
        except KeyError as e:
            raise HTTPException(404, "no such device in config.yaml") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": id, "version": save(request, w, old, text, what, device=id)}

    # --- Settings as forms ---------------------------------------------------------------------

    def has_secret(name: str) -> bool:
        return hub.secrets.has(name)

    def _masked(config) -> dict[str, Any]:
        """What the forms show: everything but secrets (tokens, keys, password hashes)."""
        data = config.model_dump(mode="json")
        for i, channel in enumerate(data["notify"]):
            channel["_id"] = i  # which channel it is: its secrets go back to it only
            if channel.get("token"):
                channel["token"] = KEEP
            if channel.get("smtp"):
                channel["smtp"] = _hide_password(channel["smtp"])
            if channel.get("webhook"):  # a Discord or Slack hook URL is its own secret
                channel["webhook"] = KEEP
        if data["vulns"].get("nvd_api_key"):
            data["vulns"]["nvd_api_key"] = KEEP
        data["users"] = {name: {"role": u["role"]} for name, u in data["users"].items()}
        return {name: {key: data.get(key) for key in keys} for name, keys in SECTIONS.items()}

    @api.get("/settings")
    def get_settings(request: Request, response: Response) -> dict[str, Any]:
        guard(request)
        response.headers["Cache-Control"] = "no-store"
        text = current()
        try:
            config = hub.check(text)
        except ConfigProblem as e:
            raise HTTPException(409, f"config.yaml has a problem; fix it in the file: {e.message}") from e
        secrets = {name: has_secret(name) for name in wg_secrets(config)}
        return {"version": config_edit.version_of(text), "sections": _masked(config), "secrets": secrets}

    def _unmask(section: str, value: dict[str, Any], old_config) -> dict[str, Any]:
        """Secrets left as KEEP get their current value back; users get password hashes."""
        if section == "notify":
            old = old_config.notify
            for ch in value.get("notify") or []:
                if not isinstance(ch, dict):
                    raise HTTPException(400, "notify: a list of channels")
                i = ch.pop("_id", None)
                was = old[i] if isinstance(i, int) and 0 <= i < len(old) else None
                kind = next((k for k in NotifyChannel.KINDS if ch.get(k)), None)
                # The same channel: the same kind, and (where it shows) the same address.
                same = was is not None and was.kind == kind
                if ch.get("webhook") == KEEP:
                    ch["webhook"] = was.webhook if same and was else None
                    if not ch["webhook"]:
                        raise HTTPException(400, "a webhook channel needs its URL again")
                if ch.get("token") == KEEP:
                    # An ntfy or Gotify token belongs to its server; a Telegram bot's to the bot,
                    # whichever chat it writes to.
                    target = ch.get(kind) if kind else None
                    if not (same and (kind == "telegram" or str(getattr(was, kind)) == str(target))):
                        raise HTTPException(
                            400, f"the {kind or 'channel'} channel's address changed: enter its token again"
                        )
                    ch["token"] = was.token
                if isinstance(ch.get("smtp"), str) and KEEP in ch["smtp"]:
                    # The recipient may change; _restore_password checks the server and user.
                    if not (same and was.smtp):
                        raise HTTPException(400, "the mail server changed: enter its password again")
                    ch["smtp"] = _restore_password(ch["smtp"], was.smtp)
        if section == "vulns" and (value.get("vulns") or {}).get("nvd_api_key") == KEEP:
            value["vulns"]["nvd_api_key"] = old_config.vulns.nvd_api_key
        if section == "users":
            users = {}
            for name, u in (value.get("users") or {}).items():
                if not isinstance(u, dict) or not re.match(r"^[a-z0-9][a-z0-9_.-]{0,31}$", str(name)):
                    raise HTTPException(400, f"{name!r}: user names are lowercase letters, digits, . _ -")
                password = u.get("password")
                if password:
                    stored = hash_password(password)
                elif name in old_config.users:
                    stored = old_config.users[name].password
                else:
                    raise HTTPException(400, f"{name}: a new user needs a password")
                users[name] = {"role": u.get("role", "observer"), "password": stored}
            value["users"] = users
        return value

    def _compact(section: str, value: dict[str, Any]) -> dict[str, Any]:
        """Defaults left out, so config.yaml says only what differs."""

        def dump(model, v):
            return model.model_validate(v).model_dump(mode="json", exclude_defaults=True)

        try:
            if section == "general":
                defaults = Config()
                return {
                    k: (None if v in (None, "") or v == getattr(defaults, k) else v) for k, v in value.items()
                }
            if section == "alarms":
                return {"alarms": dump(AlarmSettings, value.get("alarms") or {}) or None}
            if section == "logs":
                return {"logs": dump(LogSettings, value.get("logs") or {}) or None}
            if section == "device_backups":
                return {
                    "device_backups": dump(DeviceBackupSettings, value.get("device_backups") or {}) or None
                }
            if section == "hub_backup":
                return {"hub_backup": dump(HubBackupSettings, value.get("hub_backup") or {}) or None}
            if section == "wifi":
                return {"wifi": dump(WifiSettings, value.get("wifi") or {}) or None}
            if section == "internet":
                return {"internet": dump(InternetSettings, value.get("internet") or {}) or None}
            if section == "vulns":
                return {"vulns": dump(VulnSettings, value.get("vulns") or {}) or None}
            if section == "notify":
                return {"notify": [dump(NotifyChannel, c) for c in value.get("notify") or []] or None}
            if section == "networks":
                nets = value.get("networks") or {}
                return {"networks": {k: dump(NetworkSettings, v) for k, v in nets.items()} or None}
            if section == "groups":
                groups = value.get("groups") or {}
                out = {k: dump(GroupSettings, v) for k, v in groups.items()}
                for k, g in out.items():  # a name like the id says nothing more
                    if g.get("name") == k:
                        del g["name"]
                return {"groups": out or None}
            if section == "backups":
                backups = {}
                for k, v in (value.get("backups") or {}).items():
                    b = dump(BackupCheck, v)
                    if b.get("name") == k:  # a name like the id says nothing more
                        del b["name"]
                    backups[k] = b
                return {"backups": backups or None}
            if section == "maintenance":
                windows = [dump(MaintenanceWindow, w) for w in value.get("maintenance") or []]
                return {"maintenance": windows or None}
            if section == "sites":  # a site without a name needs no line: its id stands in
                return {"sites": {k: str(v) for k, v in (value.get("sites") or {}).items() if v} or None}
        except ValidationError as e:
            raise HTTPException(400, describe(e)) from e
        return value

    @api.put("/settings/{section}")
    async def put_settings(section: str, body: SectionBody, request: Request) -> dict[str, Any]:
        w = guard(request)
        keys = SECTIONS.get(section)
        if keys is None:
            raise HTTPException(404, "no such section")
        old = current()
        if body.version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since the form was opened; open it again")
        try:
            old_config = hub.check(old)
        except ConfigProblem as e:
            raise HTTPException(409, f"config.yaml has a problem; fix it in the file: {e.message}") from e
        unknown = set(body.value) - set(keys)
        if unknown:
            raise HTTPException(400, f"not part of {section}: {', '.join(sorted(unknown))}")
        try:
            value = _compact(section, _unmask(section, dict(body.value), old_config))
        except (AttributeError, TypeError) as e:  # a list where a mapping belongs, say
            raise HTTPException(400, f"{section}: not the shape it has") from e
        text = old
        try:
            for key in keys:
                if key in value:
                    text = config_edit.set_top(text, key, value[key])
        except YAMLError as e:
            raise HTTPException(400, f"config.yaml can't be read: {yaml_problem(e)}") from e
        return {"version": save(request, w, old, text, f"settings.{section}")}

    @api.put("/secrets/{name}")
    async def put_secret(name: str, body: SecretBody, request: Request) -> dict[str, Any]:
        """A secret (a password Kastellan uses, e.g. wg-easy's), written only: never shown."""
        w = guard(request)
        # Kastellan's own: OpenWrt logins and the group fingerprints' key. A VPN's form names its
        # wg-easy secret, so that name alone must not let a form overwrite one of these.
        own = {
            *OWN,
            *(hub.openwrt_secret(id) for id in hub.config.devices),
            *(hub.fritz_secret(id) for id in hub.config.devices),
        }
        mine = name.startswith(("openwrt-", "fritzbox-"))
        if name not in wg_secrets(hub.config) or name in own or mine:
            raise HTTPException(403, "that secret isn't set from here")
        try:
            hub.secrets.set(name, body.value)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e)) from e
        hub.audit.record("secret.set", w.user, client_address(request.scope), secret=name)
        return {"set": True}

    # --- VPNs: any number, of either kind (vpns:) -----------------------------------------------

    def vpns_now(text: str) -> dict[str, dict[str, Any]]:
        """The VPNs as config.yaml says them (an old `vpn:` read as `vpns:`), defaults left out."""
        try:
            config = hub.check(text)
        except ConfigProblem as e:
            raise HTTPException(409, f"config.yaml has a problem; fix it in the file: {e.message}") from e
        return {
            vid: {"kind": v.kind, **v.model_dump(mode="json", exclude_defaults=True)}
            for vid, v in config.vpns.items()
        }

    def write_vpns(request: Request, w: Who, old: str, vpns: dict[str, Any], what: str, vid: str) -> str:
        try:
            text = config_edit.set_top(old, "vpn", None)  # the one-of-each form goes: vpns: now
            text = config_edit.set_top(text, "vpns", vpns or None)
        except YAMLError as e:
            raise HTTPException(400, f"config.yaml can't be read: {yaml_problem(e)}") from e
        return save(request, w, old, text, what, vpn=vid)

    @api.put("/vpns/{vid}")
    async def put_vpn(vid: str, body: VpnBody, request: Request) -> dict[str, Any]:
        """Add a VPN, or change one: its kind's settings (secrets stay secrets: set apart)."""
        w = guard(request)
        if not ID.match(vid) or len(vid) > 40:
            raise HTTPException(400, "ids are lowercase letters, digits, - and _ (up to 40)")
        old = current()
        if body.version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since you opened it; reload, then change it again")
        vpns = vpns_now(old)
        was = vpns.get(vid)
        if body.new and was is not None:
            raise HTTPException(400, f"there is a VPN {vid!r} already")
        if not body.new and was is None:
            raise HTTPException(404, "no such VPN")
        # A change carries the form's fields only: the rest stays as the file has it now (not as
        # the page last saw it), so an edit made elsewhere meanwhile isn't undone.
        value = {**(was or {}), **body.vpn}
        kind = value.get("kind") or (was or {}).get("kind")
        if was is not None and kind != was["kind"]:
            raise HTTPException(400, "a VPN keeps its kind; add another for the other kind")
        if kind == "wireguard" and "password_secret" not in value:
            # Its own secret: a new one gets wg-easy-<id>; a changed one keeps what it had.
            default = "wg-easy" if was else f"wg-easy-{vid}"
            value["password_secret"] = (was or {}).get("password_secret", default)
        model = {"wireguard": WireGuardSource, "openvpn": OpenVpnSource}.get(kind or "")
        if model is None:
            raise HTTPException(400, "kind: wireguard or openvpn")
        try:
            entry = model.model_validate({**value, "kind": kind}).model_dump(
                mode="json", exclude_defaults=True
            )
        except ValidationError as e:
            raise HTTPException(400, describe(e)) from e
        vpns[vid] = {"kind": kind, **entry}
        return {
            "id": vid,
            "version": write_vpns(request, w, old, vpns, "vpn.add" if body.new else "vpn.change", vid),
        }

    @api.delete("/vpns/{vid}")
    async def delete_vpn(vid: str, request: Request, version: str | None = None) -> dict[str, Any]:
        """Kastellan stops managing this VPN's clients (the VPN itself and its clients stay)."""
        w = guard(request)
        old = current()
        if version is not None and version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since you opened it; reload, then change it again")
        vpns = vpns_now(old)
        if vpns.pop(vid, None) is None:
            raise HTTPException(404, "no such VPN")
        return {"version": write_vpns(request, w, old, vpns, "vpn.remove", vid)}

    @api.post("/devices")
    async def add_device(body: NewDevice, request: Request) -> dict[str, Any]:
        return edit(request, body.id, form_fields(body), True, "device.add", body.version)

    @api.put("/devices/{id}")
    async def change_device(id: str, body: DeviceForm, request: Request) -> dict[str, Any]:
        return edit(request, id, form_fields(body), False, "device.change", body.version)

    @api.delete("/devices/{id}")
    async def delete_device(id: str, request: Request, version: str | None = None) -> dict[str, Any]:
        return edit(request, id, None, False, "device.delete", version)

    @api.put("/devices/{id}/powered")
    async def set_powered(id: str, body: Powered, request: Request) -> dict[str, Any]:
        """Which devices UPS `id` powers: each device's `powered_by` set or taken away, in one
        save (checked and audited like any edit)."""
        w = guard(request)
        ups = hub.config.devices.get(id)
        if ups is None or ups.kind is not Kind.UPS:
            raise HTTPException(404, "no such UPS device")
        unknown = [d for d in body.devices if d not in hub.config.devices or d == id]
        if unknown:
            raise HTTPException(400, f"not a device it can power: {', '.join(unknown[:5])}")
        old = current()
        if body.version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since the page was loaded; load it again")
        text, wanted = old, set(body.devices)
        try:
            for did, device in hub.config.devices.items():
                if did in wanted and device.powered_by != id:
                    text = config_edit.set_device_key(text, did, "powered_by", id)
                elif did not in wanted and device.powered_by == id:
                    text = config_edit.set_device_key(text, did, "powered_by", None)
        except YAMLError as e:
            raise HTTPException(409, f"config.yaml can't be read: {yaml_problem(e)}") from e
        except KeyError as e:  # loaded, but not (or no longer) in the file as written
            raise HTTPException(
                409, f"{e.args[0] if e.args else 'a device'} isn't in config.yaml as written"
            ) from e
        return {"version": save(request, w, old, text, "ups.powered", device=id)}

    @api.get("/devices/{id}/yaml")
    def get_device_yaml(id: str, request: Request, response: Response) -> dict[str, Any]:
        """The device's part of config.yaml as written, for editing it as text."""
        guard(request)
        response.headers["Cache-Control"] = "no-store"
        text = current()
        try:
            block = config_edit.device_text(text, id)
        except YAMLError as e:
            raise HTTPException(409, f"config.yaml can't be read: {yaml_problem(e)}") from e
        except KeyError as e:
            raise HTTPException(404, "no such device in config.yaml") from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"id": id, "text": block, "version": config_edit.version_of(text)}

    @api.put("/devices/{id}/yaml")
    async def put_device_yaml(id: str, body: FileBody, request: Request) -> dict[str, Any]:
        w = guard(request)
        old = current()
        if body.version != config_edit.version_of(old):
            raise HTTPException(409, "config.yaml changed since you opened it; reload, then change it again")
        try:
            new = config_edit.set_device_text(old, id, body.text)
        except YAMLError as e:
            raise HTTPException(400, f"config.yaml can't be read: {yaml_problem(e)}") from e
        except KeyError as e:
            raise HTTPException(404, "no such device in config.yaml") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"id": id, "version": save(request, w, old, new, "device.change", device=id)}

    @api.get("/devices/{id}/form")
    def device_form(id: str, request: Request) -> dict[str, Any]:
        """The device as config.yaml says it (not as the loaded config fills in defaults, and
        not an older loaded version), with the file's version: saving refuses if it changed."""
        guard(request)
        text = current()
        try:
            data = YAML(typ="safe").load(text) or {}
        except YAMLError as e:
            raise HTTPException(409, f"config.yaml can't be read: {yaml_problem(e)}") from e
        raw = (data.get("devices") or {}).get(id) if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            raise HTTPException(404, "no such device in config.yaml")
        checks = []
        for item in raw.get("checks", ["ping"]) or []:
            try:
                c = Check.model_validate(item)
            except ValueError:
                continue
            checks.append(
                {
                    "type": c.type.value,
                    "target": c.target,
                    # Only a name the file gives: a default one follows the target.
                    "name": item.get("name", "") if isinstance(item, dict) else "",
                    "expect": c.expect,
                    "contains": c.contains,
                    "server": c.server,
                    "verify": c.verify,
                    "tls": c.tls,
                    "interval": c.interval,
                    "timeout": c.timeout,
                    "exposed": c.exposed,
                }
            )
        return {
            "id": id,
            "name": raw.get("name") or "",
            "host": raw.get("host") or "",
            "kind": raw.get("kind") or "other",
            "site": raw.get("site") or "",
            "runs_on": raw.get("runs_on") if isinstance(raw.get("runs_on"), dict) else None,
            "ups": raw.get("ups") if isinstance(raw.get("ups"), dict) else None,
            "powered_by": raw.get("powered_by") or None,
            "tags": [str(t) for t in raw.get("tags") or []],
            "always_on": raw.get("always_on"),
            "exposed": bool(raw.get("exposed", False)),
            "checks": checks,
            "version": config_edit.version_of(text),
        }

    return api
