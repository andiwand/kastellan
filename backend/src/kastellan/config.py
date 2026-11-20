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

A check is `ping`, `tcp: <port>` or `http: <url>`. A device without `checks` is pinged. An https
check also looks at the certificate; `tls:` decides whether it can raise alarms (tls.py).

    users:
      andreas:
        role: admin           # or observer
        password: scrypt$...  # from `kastellan hash-password`
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

from .tls import public_name

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
    # https: alarm when the certificate is not valid or ends soon. Default: for names on the
    # internet (not addresses, not .lan, .local, .home.arpa, ...) and checks with verify: true.
    tls: bool | None = None

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
        if self.tls and not (isinstance(self.target, str) and self.target.startswith("https://")):
            raise ValueError("tls: true is for https checks")
        if self.name.startswith("@"):
            raise ValueError("check names can't start with @ (kept for firmware and containers)")
        return self

    @property
    def watch_cert(self) -> bool:
        """Whether this check's certificate can raise an alarm."""
        if not (isinstance(self.target, str) and self.target.startswith("https://")):
            return False
        if self.tls is not None:
            return self.tls
        return self.verify or public_name(urlsplit(self.target).hostname or "")


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
    # A WUD trigger that updates a container (e.g. docker.local); only used for automatic updates
    # that vulns.py allows (see `auto_update`).
    trigger: str | None = Field(default=None, pattern=r"^[a-z]+\.[A-Za-z0-9_-]+$")


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
    # Let Kastellan update a container by itself, only when the device is exposed and a confirmed
    # high, critical or exploited vulnerability has a fix (see vulns.py). Off by default.
    auto_update: bool = False
    # Which networks it is in, when its address doesn't say (networks.py): one of `networks:`, or
    # several (a router: [home, internet]; a VPN server: [home, vpn]). "internet" is built in.
    network: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _one_network(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("network"), str):
            data = {**data, "network": [data["network"]]}
        return data

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
    cert_days: int = Field(default=14, ge=1, le=365)  # a watched certificate ending sooner: warning


class NotifyChannel(BaseModel):
    """Where alarms go (notify.py): exactly one of ntfy, webhook, gotify, telegram or email."""

    model_config = ConfigDict(extra="forbid")

    ntfy: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    webhook: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    gotify: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")  # the server
    telegram: str | int | None = None  # the chat id (a bot's token in `token`)
    email: str | None = Field(default=None, pattern=r"^[^@\s,]+@[^@\s,]+$")  # to whom
    # smtp://user:password@host:587 (STARTTLS) or smtps://...:465 (TLS); for email.
    smtp: str | None = Field(default=None, pattern=r"^smtps?://[^\s]+$")
    sender: str | None = Field(default=None, pattern=r"^[^@\s,]+@[^@\s,]+$")  # email: From
    token: str | None = None  # ntfy access token, Gotify app token, Telegram bot token
    min: Literal["info", "warning", "critical"] = "warning"
    quiet: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")  # only critical then

    KINDS: ClassVar[tuple[str, ...]] = ("ntfy", "webhook", "gotify", "telegram", "email")

    @property
    def kind(self) -> str:
        return next(k for k in self.KINDS if getattr(self, k) is not None)

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if sum(getattr(self, k) is not None for k in self.KINDS) != 1:
            raise ValueError("a notify channel is one of ntfy:, webhook:, gotify:, telegram: or email:")
        if self.gotify is not None and not self.token:
            raise ValueError("gotify: needs token: (an application token from Gotify)")
        if self.telegram is not None:
            if not self.token or not re.match(r"^\d+:[\w-]+$", self.token):
                raise ValueError("telegram: needs token: (the bot's, like 123456:ABC-...)")
            if not re.match(r"^-?\d+$|^@\w+$", str(self.telegram)):
                raise ValueError("telegram: is the chat id (a number, or @channelname)")
        if self.email is not None and not self.smtp:
            raise ValueError("email: needs smtp: (smtp://user:password@host:587 or smtps://...:465)")
        if (self.smtp or self.sender) and self.email is None:
            raise ValueError("smtp: and sender: are for email: channels")
        if self.quiet:
            from datetime import time

            try:
                for part in self.quiet.split("-"):
                    time.fromisoformat(part)
            except ValueError as e:
                raise ValueError(f"quiet: {self.quiet!r} is not HH:MM-HH:MM") from e
        return self


class NetworkSettings(BaseModel):
    """A local network by name (networks.py): devices whose address is in `cidr` are in it."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    cidr: str | None = None

    @model_validator(mode="after")
    def _cidr(self) -> Self:
        if self.cidr is not None:
            import ipaddress

            try:
                ipaddress.ip_network(self.cidr, strict=False)
            except ValueError as e:
                raise ValueError(f"cidr: {self.cidr!r} is not a network like 192.168.1.0/24") from e
        return self


class VulnSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    every_hours: float = Field(default=6, ge=1)
    nvd_api_key: str | None = None  # optional: faster NVD lookups


class WireGuardSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wg_easy: str = Field(pattern=r"^https?://[^\s]+$")  # wg-easy's address, e.g. http://sv1:51821
    user: str = "admin"
    password_secret: str = "wg-easy"  # data/secrets/<name> (kastellan secret set wg-easy)


class OpenVpnSource(BaseModel):
    """OpenVPN clients through an easy-rsa PKI (vpn/openvpn.py).

    mode restricted: the hub's VPN key may only run kastellan-pki / kastellan-crl on the hosts
    (installed there; `pki_user` and `server_user` are those restricted logins).
    mode direct: nothing is installed; the same scripts are sent over the devices' normal
    `ssh:` logins on every call."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["restricted", "direct"] = "restricted"
    pki: str  # the device with the easy-rsa PKI
    server: str | None = None  # the OpenVPN server's device (receives the CRL)
    days: int = Field(default=365, ge=1, le=3650)  # how long new client certificates last
    # Not "kastellan": that is the name Kastellan's own login (with sudo) gets on a device.
    pki_user: str = Field(default="kastellan-pki", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    server_user: str = Field(default="root", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    # On the PKI host
    easyrsa_dir: str = Field(default="/etc/openvpn/easy-rsa", pattern=r"^/[\w./-]+$")
    template_path: str = Field(default="/etc/kastellan-pki/client.ovpn", pattern=r"^/[\w./-]+$")
    tls_key: str | None = Field(default=None, pattern=r"^/[\w./-]+$")  # tls-crypt/tls-auth key to inline
    reserved: list[str] = Field(default_factory=lambda: ["server"])  # certificate names that aren't clients
    # The client config with {{ca}} {{cert}} {{key}} {{tls}}; installed to template_path if given.
    client_template: str | None = None
    # On the OpenVPN server
    crl_path: str = Field(default="/etc/openvpn/crl.pem", pattern=r"^/[\w./-]+$")
    ca_path: str = Field(default="/etc/openvpn/ca.crt", pattern=r"^/[\w./-]+$")

    @model_validator(mode="after")
    def _reserved_names(self) -> Self:
        for name in self.reserved:
            if not re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", name):
                raise ValueError(f"reserved: {name!r} is not a certificate name")
        return self


class VpnSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wireguard: WireGuardSource | None = None
    openvpn: OpenVpnSource | None = None


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_interval: float = Field(default=60, ge=5)
    retention_days: int = Field(default=30, ge=1)
    devices: dict[str, Device] = Field(default_factory=dict)
    # Sign-in is off while this is empty; see auth.py.
    users: dict[str, User] = Field(default_factory=dict)
    alarms: AlarmSettings = Field(default_factory=AlarmSettings)
    notify: list[NotifyChannel] = Field(default_factory=list)
    vulns: VulnSettings = Field(default_factory=VulnSettings)
    vpn: VpnSettings = Field(default_factory=VpnSettings)
    networks: dict[str, NetworkSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _vpn_devices(self) -> Self:
        ovpn = self.vpn.openvpn
        for role, id in (("pki", ovpn.pki if ovpn else None), ("server", ovpn.server if ovpn else None)):
            if id is None:
                continue
            if id not in self.devices:
                raise ValueError(f"vpn.openvpn.{role}: {id!r} is not a device")
            if ovpn and ovpn.mode == "direct" and self.devices[id].ssh is None:
                raise ValueError(f"vpn.openvpn.{role}: {id!r} needs ssh: (its login) in direct mode")
        return self

    @model_validator(mode="after")
    def _ids_and_parents(self) -> Self:
        for id, device in self.devices.items():
            if not ID.match(id):
                raise ValueError(f"device id {id!r}: use lowercase letters, digits, - and _")
            if not device.name:
                device.name = id
            for net in device.network:
                if net != "internet" and net not in self.networks:
                    raise ValueError(f"device {id!r}: network {net!r} is not in networks: (or internet)")
            if device.parent is not None and device.parent not in self.devices:
                raise ValueError(f"device {id!r}: parent {device.parent!r} is not a device")
        return self


def load(path: Path) -> Config:
    """Read config.yaml; a missing or empty file is an empty configuration."""
    if not path.exists():
        return Config()
    data = YAML(typ="safe").load(path.read_text()) or {}
    return Config.model_validate(data)
