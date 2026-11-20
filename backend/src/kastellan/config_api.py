"""Changing config.yaml from the app (config_edit.py): the whole file, and one device at a time.

Only with sign-in on, and only admins: config.yaml holds the users, and the VPN and SSH
settings; while sign-in is off anyone who reaches the hub would be an admin. Every change goes
to the audit log with what changed (password hashes masked).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from . import config_edit
from .auth_api import Who, client_address, require_signin
from .config import Check, Kind, Role
from .hub_errors import yaml_problem

if TYPE_CHECKING:
    from .hub import Hub


class FileBody(BaseModel):
    text: str = Field(max_length=1_000_000)
    version: str | None = None  # from GET; None only when checking


class CheckForm(BaseModel):
    type: Literal["ping", "tcp", "http"]
    target: str | int | None = None
    name: str = Field(default="", max_length=80)
    expect: int | None = Field(default=None, ge=100, le=599)
    verify: bool = False
    tls: bool | None = None
    interval: float | None = Field(default=None, ge=5, le=86400)
    timeout: float | None = Field(default=None, gt=0, le=60)


class DeviceForm(BaseModel):
    name: str = Field(default="", max_length=120)
    host: str = Field(min_length=1, max_length=253)
    kind: Kind = Kind.OTHER
    tags: list[str] = Field(default_factory=list, max_length=30)
    always_on: bool | None = None
    exposed: bool = False
    checks: list[CheckForm] = Field(default_factory=list, max_length=50)
    version: str | None = None  # from the form: saving refuses if the file changed since


class NewDevice(DeviceForm):
    id: str = Field(min_length=1, max_length=64)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api")

    def guard(request: Request) -> Who:
        w = require_signin(request)
        if hub.demo:
            raise HTTPException(409, "demo mode shows a made-up network; its config can't be changed")
        return w

    def save(request: Request, w: Who, old: str, new: str, what: str, **details: Any) -> str:
        from .hub import ConfigProblem

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
        from .hub import ConfigProblem

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

    @api.post("/devices")
    async def add_device(body: NewDevice, request: Request) -> dict[str, Any]:
        return edit(request, body.id, form_fields(body), True, "device.add", body.version)

    @api.put("/devices/{id}")
    async def change_device(id: str, body: DeviceForm, request: Request) -> dict[str, Any]:
        return edit(request, id, form_fields(body), False, "device.change", body.version)

    @api.delete("/devices/{id}")
    async def delete_device(id: str, request: Request, version: str | None = None) -> dict[str, Any]:
        return edit(request, id, None, False, "device.delete", version)

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
                    "verify": c.verify,
                    "tls": c.tls,
                    "interval": c.interval,
                    "timeout": c.timeout,
                }
            )
        return {
            "id": id,
            "name": raw.get("name") or "",
            "host": raw.get("host") or "",
            "kind": raw.get("kind") or "other",
            "tags": [str(t) for t in raw.get("tags") or []],
            "always_on": raw.get("always_on"),
            "exposed": bool(raw.get("exposed", False)),
            "checks": checks,
            "version": config_edit.version_of(text),
        }

    return api
