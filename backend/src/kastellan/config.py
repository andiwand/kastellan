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
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

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
        return self


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
    checks: list[Check] = Field(default_factory=lambda: [Check(type=CheckType.PING)])

    @model_validator(mode="after")
    def _unique_checks(self) -> Self:
        if self.always_on is None:
            self.always_on = self.kind not in (Kind.PC, Kind.LAPTOP)
        names = [c.name for c in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("two checks have the same name; give one a `name`")
        return self


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_interval: float = Field(default=60, ge=5)
    retention_days: int = Field(default=30, ge=1)
    devices: dict[str, Device] = Field(default_factory=dict)

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
