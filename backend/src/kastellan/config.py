"""config.yaml: the devices and how to check them. It is the only configuration; there is no database.

    devices:
      fritzbox:
        name: FRITZ!Box 7590
        host: 192.168.178.1
        kind: router
        checks:
          - ping
          - http: https://192.168.178.1
      nas:
        host: nas.lan
        kind: server
        parent: fritzbox      # what it hangs off, for the graph
        checks:
          - ping
          - tcp: 22
            name: ssh
          - http: http://nas.lan:8096/health
            name: jellyfin

A check is `ping`, `tcp: <port>` or `http: <url>`. A device without `checks` is pinged.

    users:
      andreas:
        role: admin           # or observer
        password: scrypt$...  # from `kastellan hash-password`
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class Kind(StrEnum):
    ROUTER = "router"
    ACCESS_POINT = "access-point"
    SWITCH = "switch"
    SERVER = "server"
    PC = "pc"
    LAPTOP = "laptop"
    PRINTER = "printer"
    CAMERA = "camera"
    OTHER = "other"


class CheckType(StrEnum):
    PING = "ping"
    TCP = "tcp"
    HTTP = "http"


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: CheckType
    target: str | int | None = None  # the port for tcp, the URL for http
    name: str = ""
    interval: float | None = Field(default=None, ge=5)  # seconds; None: the global check_interval
    timeout: float = Field(default=5, gt=0, le=60)
    # Devices on a home network mostly have self-signed certificates, and a check sends no
    # credentials, so it does not verify them unless asked to.
    verify: bool = False
    expect: int | None = None  # http: this status, else any status below 500 counts as up

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {data: None}
        if not isinstance(data, dict) or "type" in data:
            return data
        kinds = [t for t in CheckType if t.value in data]
        if len(kinds) != 1:
            raise ValueError("a check is one of: ping, tcp: <port>, http: <url>")
        data = dict(data)
        kind = kinds[0]
        return {"type": kind, "target": data.pop(kind.value), **data}

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if self.type is CheckType.TCP and not (isinstance(self.target, int) and 1 <= self.target <= 65535):
            raise ValueError("tcp needs a port number (1 to 65535)")
        if self.type is CheckType.HTTP and not (
            isinstance(self.target, str) and self.target.startswith(("http://", "https://"))
        ):
            raise ValueError("http needs a URL starting with http:// or https://")
        if not self.name:
            self.name = self.type.value if self.type is CheckType.PING else f"{self.type.value}:{self.target}"
        if self.name.startswith("@"):
            raise ValueError("check names can't start with @ (kept for firmware and containers)")
        return self


class SshAccess(BaseModel):
    """How the hub logs in to a device (terminal, and later what it reads over SSH)."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    port: int = Field(default=22, ge=1, le=65535)


class DesktopAccess(BaseModel):
    """Remote desktop through guacd. The password is asked for when connecting, never stored."""

    model_config = ConfigDict(extra="forbid")

    protocol: Literal["rdp", "vnc"]
    port: int | None = Field(default=None, ge=1, le=65535)  # None: 3389 for RDP, 5900 for VNC
    user: str | None = None  # suggested in the connect form
    # RDP: which security to negotiate; "any" lets the PC choose (NLA on current Windows).
    security: Literal["any", "nla", "tls", "rdp"] = "any"
    # Windows' own RDP certificate is self-signed, and guacd can't pin it; set false once the PC has
    # a certificate guacd can verify.
    ignore_cert: bool = True


class DockerSource(BaseModel):
    """Where the host's containers and their updates come from (for now WUD's API)."""

    model_config = ConfigDict(extra="forbid")

    wud: str = Field(pattern=r"^https?://[^\s]+$")


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    host: str
    kind: Kind = Kind.OTHER
    parent: str | None = None
    tags: list[str] = Field(default_factory=list)
    # Down is a problem only for devices that should always be on; a laptop that is off is just
    # offline. None: by kind (pcs and laptops are not always on).
    always_on: bool | None = None
    ssh: SshAccess | None = None  # set to open a terminal on the device
    desktop: DesktopAccess | None = None  # set to open its screen in the browser
    driver: str | None = None  # what else Kastellan can ask it, e.g. fritzbox (see drivers/)
    docker: DockerSource | None = None  # its containers and their updates
    # Reachable from the internet (a port forward, a public server): known vulnerabilities in
    # what it runs count as urgent, and may trigger automatic updates (see vulns.py).
    exposed: bool = False
    checks: list[Check] = Field(default_factory=lambda: [Check(type=CheckType.PING)])

    @model_validator(mode="after")
    def _unique_checks(self) -> Self:
        if self.driver is not None:
            from .drivers import DRIVERS

            if self.driver not in DRIVERS:
                raise ValueError(
                    f"driver: {self.driver!r} is unknown; there are {', '.join(sorted(DRIVERS))}"
                )
        if self.always_on is None:
            self.always_on = self.kind not in (Kind.PC, Kind.LAPTOP)
        names = [c.name for c in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("two checks have the same name; give one a `name`")
        return self


class Role(StrEnum):
    OBSERVER = "observer"  # sees everything, changes nothing, opens no sessions
    ADMIN = "admin"


class User(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role = Role.OBSERVER
    password: str  # a hash from `kastellan hash-password`, never the password itself

    @model_validator(mode="after")
    def _hashed(self) -> Self:
        parts = self.password.split("$")
        try:
            weak = len(parts) != 6 or int(parts[1]) < 2**14 or int(parts[2]) < 8 or int(parts[3]) < 1
        except ValueError:
            weak = True
        if parts[0] == "scrypt" and weak:
            raise ValueError("password: this scrypt hash is malformed or too weak; make a new one")
        if not self.password.startswith("scrypt$"):
            raise ValueError("password: put the hash from `kastellan hash-password` here, not the password")
        return self


class AlarmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    down_after: int = Field(default=180, ge=30)  # seconds down before an alarm


class NotifyChannel(BaseModel):
    """Where alarms go (notify.py). One of ntfy or webhook."""

    model_config = ConfigDict(extra="forbid")

    ntfy: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    webhook: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    token: str | None = None  # ntfy access token
    min: Literal["info", "warning", "critical"] = "warning"
    quiet: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")  # only critical then

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.ntfy is None) == (self.webhook is None):
            raise ValueError("a notify channel is either ntfy: <url> or webhook: <url>")
        return self


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_interval: float = Field(default=60, ge=5)
    retention_days: int = Field(default=30, ge=1)
    devices: dict[str, Device] = Field(default_factory=dict)
    # Sign-in is off while this is empty; see auth.py.
    users: dict[str, User] = Field(default_factory=dict)
    alarms: AlarmSettings = Field(default_factory=AlarmSettings)
    notify: list[NotifyChannel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_and_parents(self) -> Self:
        for id, device in self.devices.items():
            if not ID.match(id):
                raise ValueError(f"device id {id!r}: use lowercase letters, digits, - and _")
            if not device.name:
                device.name = id
            if device.parent is not None and device.parent not in self.devices:
                raise ValueError(f"device {id!r}: parent {device.parent!r} is not a device")
        return self


def load(path: Path) -> Config:
    """Read config.yaml; a missing or empty file is an empty configuration."""
    if not path.exists():
        return Config()
    data = YAML(typ="safe").load(path.read_text()) or {}
    return Config.model_validate(data)
