"""config.yaml: the devices and how to check them. It is the only configuration; the SQLite files
(logs, vitals) hold only what was seen, never settings.

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

A check is `ping`, `tcp: <port>`, `http: <url>`, `dns: <name>` or `dhcp`. A device without
`checks` is pinged. An https check also looks at the certificate; `tls:` decides whether it can
raise alarms (tls.py).

    users:
      andreas:
        role: admin           # or observer
        password: scrypt$...  # from `kastellan hash-password`
"""

from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

from .secrets import OWN
from .tls import public_name

ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# A libvirt VM's name as Kastellan takes it (the actor, config's runs_on): libvirt allows more,
# but never a number or a UUID, which virsh reads as a domain id or UUID before a name.
VM_NAME = re.compile(
    r"(?!\d+$)(?![0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$)"
    r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}"
)
HOME = "home"  # the site of a device without `site:`


class Kind(StrEnum):
    ROUTER = "router"
    ACCESS_POINT = "access-point"
    SWITCH = "switch"
    SERVER = "server"
    PC = "pc"
    LAPTOP = "laptop"
    PRINTER = "printer"
    CAMERA = "camera"
    UPS = "ups"
    OTHER = "other"


class CheckType(StrEnum):
    PING = "ping"
    TCP = "tcp"
    HTTP = "http"
    DNS = "dns"  # does a name resolve (to what): checks.py
    DHCP = "dhcp"  # does the device's DHCP server answer (and nobody else's): dhcp.py


LABEL = r"[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9])?"
HOSTNAME = re.compile(rf"^(?=.{{1,253}}$){LABEL}(\.{LABEL})*\.?$")  # a name to look up (dns)


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: CheckType
    target: str | int | None = None  # the port for tcp, the URL for http, the name for dns
    name: str = ""
    interval: float | None = Field(default=None, ge=5)  # seconds; None: the global check_interval
    timeout: float = Field(default=5, gt=0, le=60)
    # Devices on a home network mostly have self-signed certificates, and a check sends no
    # credentials, so it does not verify them unless asked to.
    verify: bool = False
    # http: this status, else any status below 500 counts as up. dns: an address (or several)
    # that must be among the answers.
    expect: int | str | list[str] | None = None
    # http: text the page must contain (in its first 256 KB) to count as up: a page that answers
    # but shows an error ("Bad gateway", a login loop) is down.
    contains: str | None = Field(default=None, min_length=1, max_length=200)
    server: str | None = None  # dns: the resolver to ask (an address); default: the system's
    # https: alarm when the certificate is not valid or ends soon. Default: for names on the
    # internet (not addresses, not .lan, .local, .home.arpa, ...) and checks with verify: true.
    tls: bool | None = None
    # Reachable from the internet (a port forward, a reverse proxy): this service, not the whole
    # device. Vulnerabilities in what it runs count as urgent (vuln_policy.py).
    exposed: bool = False

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, data: Any) -> Any:
        if isinstance(data, str):
            data = {data: None}
        if not isinstance(data, dict) or "type" in data:
            return data
        kinds = [t for t in CheckType if t.value in data]
        if len(kinds) != 1:
            raise ValueError("a check is one of: ping, tcp: <port>, http: <url>, dns: <name>, dhcp")
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
        if self.type is CheckType.DNS:
            if not (isinstance(self.target, str) and HOSTNAME.match(self.target)):
                raise ValueError("dns needs a name to look up, like example.org")
            if isinstance(self.expect, str):
                self.expect = [self.expect]
            if self.expect is not None and not isinstance(self.expect, list):
                raise ValueError("dns: expect is an address or a list of addresses")
            for address in [*(self.expect or []), *([self.server] if self.server else [])]:
                try:
                    ipaddress.ip_address(address)
                except ValueError as e:
                    raise ValueError(f"dns: {address!r} is not an address") from e
        elif isinstance(self.expect, str | list):
            raise ValueError("expect: a status code for http, addresses for dns")
        if self.contains is not None and self.type is not CheckType.HTTP:
            raise ValueError("contains: is for http checks")
        if self.server and self.type is not CheckType.DNS:
            raise ValueError("server: is for dns checks")
        if self.type is CheckType.DHCP and self.target is not None:
            raise ValueError("dhcp takes nothing: it asks the network and expects the device to answer")
        if not self.name:
            self.name = (
                self.type.value
                if self.type in (CheckType.PING, CheckType.DHCP)
                else f"{self.type.value}:{self.target}"
            )
        if self.tls and not (isinstance(self.target, str) and self.target.startswith("https://")):
            raise ValueError("tls: true is for https checks")
        if self.name.startswith("@"):
            raise ValueError("check names can't start with @ (kept for firmware and containers)")
        return self

    @property
    def hostname(self) -> str | None:
        """The host an http check's URL names (None for ping and tcp: they go to the device)."""
        if self.type is CheckType.HTTP and isinstance(self.target, str):
            return urlsplit(self.target).hostname or ""
        return None

    @property
    def watch_cert(self) -> bool:
        """Whether this check's certificate can raise an alarm."""
        if not (isinstance(self.target, str) and self.target.startswith("https://")):
            return False
        if self.tls is not None:
            return self.tls
        return self.verify or public_name(self.hostname or "")


class RunsOn(BaseModel):
    """The device is a virtual machine on another device (libvirt): which host, which VM. Kept
    apart from `parent:` (the network path): alarms fold it into its host when that is down."""

    model_config = ConfigDict(extra="forbid")
    host: str  # a configured device's id
    vm: str  # the VM's name in libvirt

    @model_validator(mode="after")
    def _names(self) -> Self:
        if not VM_NAME.fullmatch(self.vm):
            raise ValueError("runs_on.vm: letters, digits and _ . + - (not a number or a UUID)")
        return self


class UpsLink(BaseModel):
    """Which host reads the UPS (its collector: apcupsd's apcaccess, or NUT's upsc) and, with NUT,
    which of its UPSes this is. A UPS on USB or serial has no address of its own."""

    model_config = ConfigDict(extra="forbid")
    via: str  # a configured device's id: the host it is plugged into
    name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # NUT's


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


class CollectAccess(BaseModel):
    """The collector's login (host/collector.py): its key may run the read-only script, nothing
    else."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(default="kastellan-collect", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int | None = Field(default=None, ge=1, le=65535)  # None: the ssh: port, else 22


class ActAccess(BaseModel):
    """The actor's login (host/actor.py): its key may run the dispatcher, which starts only the
    abilities installed on the host."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(default="kastellan-act", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int | None = Field(default=None, ge=1, le=65535)  # None: the ssh: port, else 22


class OpenWrtAccess(BaseModel):
    """An OpenWrt device through its own API (openwrt/): Kastellan's rpcd login there."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(default="kastellan", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int = Field(default=443, ge=1, le=65535)  # uhttpd's HTTPS
    password_secret: str | None = None  # default: openwrt-<device id> (made at setup)


class FritzBoxAccess(BaseModel):
    """A FRITZ!Box through TR-064 over HTTPS (fritzbox/): a FRITZ!Box user of Kastellan's own."""

    model_config = ConfigDict(extra="forbid")

    user: str = Field(pattern=r"^[A-Za-z0-9_.@-]{1,64}$")
    port: int = Field(default=49443, ge=1, le=65535)
    password_secret: str | None = None  # default: fritzbox-<device id>


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def faces_internet(self, where: str | None = None) -> bool:
        """Whether this (or, with `where`, what a finding was in: "service <check>", "firmware",
        "container <name>") can be reached from the internet: the device as a whole is exposed,
        or the service is (ticked, or addressed by a public name or address: the ways in the
        network view shows); the firmware and containers whenever any service of the device is.
        The device's own address counts when it is a public IPv4 one (a global IPv6 address may
        be at home; networks.public_ways decides that from where the hub is)."""
        if self.exposed:
            return True
        from .networks import is_public  # networks.py imports this module

        own = is_public(self.host) and ":" not in self.host

        def reachable(check: Check) -> bool:
            if check.exposed:
                return True
            if check.type in (CheckType.PING, CheckType.DNS, CheckType.DHCP):
                return False  # not a service of the device
            target = check.hostname or self.host
            return public_name(target) or (own if target == self.host else is_public(target))

        if where and where.startswith("service "):
            name = where.removeprefix("service ")
            return any(reachable(c) for c in self.checks if c.name == name)
        return any(reachable(c) for c in self.checks)

    name: str = ""
    host: str = ""  # needed, except for a UPS read through its host (ups.via): it has none
    kind: Kind = Kind.OTHER
    site: str | None = None  # where it is (an id: home, hetzner, ...); None: the home site
    parent: str | None = None
    runs_on: RunsOn | None = None  # a virtual machine on another device
    ups: UpsLink | None = None  # a UPS: which host reads it (only for kind ups)
    powered_by: str | None = None  # the UPS it hangs on, power-wise (a device of kind ups)
    tags: list[str] = Field(default_factory=list)
    # Down is a problem only for devices that should always be on; a laptop that is off is just
    # offline. None: by kind (pcs and laptops are not always on).
    always_on: bool | None = None
    ssh: SshAccess | None = None  # set to open a terminal on the device
    desktop: DesktopAccess | None = None  # set to open its screen in the browser
    driver: str | None = None  # what else Kastellan can ask it, e.g. fritzbox (see drivers/)
    docker: DockerSource | None = None  # its containers and their updates, from WUD
    collect: CollectAccess | None = None  # how the host is doing (and its containers)
    act: ActAccess | None = None  # what Kastellan may change there (as the host allows)
    openwrt: OpenWrtAccess | None = None  # an OpenWrt router or access point, through its API
    fritzbox: FritzBoxAccess | None = None  # a FRITZ!Box, through TR-064 with its own user
    # Its web interface (scheme://host:port), when not its first http check's (webproxy.py).
    web: str | None = Field(default=None, pattern=r"^https?://[^\s/?#]+/?$")
    # Reachable from the internet (a port forward, a public server): known vulnerabilities in
    # what it runs count as urgent, and may trigger automatic updates (see vulns.py).
    exposed: bool = False  # the whole device is reachable from the internet (a check can be too)
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
        if isinstance(data, dict) and isinstance(data.get("network"), list):
            data = {**data, "network": list(dict.fromkeys(data["network"]))}  # once each
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
        if self.ups is not None and self.kind is not Kind.UPS:
            raise ValueError("ups: is for devices of kind ups")
        if not self.host:
            # Only a UPS read through its host has no address: nothing to check, its state comes
            # from that host's report.
            if self.ups is None:
                raise ValueError("host: needed (an address or a name)")
            if "checks" in self.model_fields_set and self.checks:
                raise ValueError("checks: need a host (a UPS without one is read through ups.via)")
            self.checks = []
            # Everything that logs in to or talks to the device needs its address (else "" would
            # mean this machine: the hub's own).
            needing = ("ssh", "desktop", "collect", "act", "openwrt", "driver", "docker", "web")
            if given := [k for k in needing if getattr(self, k) is not None]:
                raise ValueError(f"{', '.join(given)}: need a host (this device has no address)")
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
    # The collector's hosts (host/collector.py): a filesystem fuller than this (percent).
    disk_warning: int = Field(default=85, ge=50, le=99)
    disk_critical: int = Field(default=95, ge=50, le=100)
    disk_temperature: int = Field(default=55, ge=30, le=90)  # °C a disk may get (SMART)
    # A filesystem full within this many days at the rate it fills: warning (0: never).
    disk_days: int = Field(default=14, ge=0, le=365)
    # A UPS on battery: critical below this charge (percent) or runtime left (minutes).
    ups_charge_critical: int = Field(default=30, ge=5, le=95)
    ups_runtime_critical: int = Field(default=5, ge=1, le=120)
    # An alarm nobody acknowledged is sent again this often (hours); 0: never.
    remind_hours: int = Field(default=0, ge=0, le=24 * 7)


class SyslogSettings(BaseModel):
    """The open syslog port (logs.py). Plain syslog is unencrypted and anyone on the network can
    forge it: off by default; lines only from known devices' addresses, kept as unverified."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    port: int = Field(default=5514, ge=1, le=65535)  # 514 needs root: map 514 -> 5514 in Docker


class SyslogTlsSettings(BaseModel):
    """Syslog over TLS (syslog_tls.py): encrypted, each sender with a client certificate
    Kastellan issued to that device. Off by default."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    port: int = Field(default=6514, ge=1, le=65535)


class LogSettings(BaseModel):
    """Central logs (logs.py): how long and how many lines are kept."""

    model_config = ConfigDict(extra="forbid")

    days: int = Field(default=14, ge=1, le=365)
    max_lines: int = Field(default=1_000_000, ge=10_000, le=20_000_000)
    syslog: SyslogSettings = Field(default_factory=SyslogSettings)
    syslog_tls: SyslogTlsSettings = Field(default_factory=SyslogTlsSettings)


class HubBackupTarget(BaseModel):
    """Where Kastellan's own backup is copied: a folder (`path`, e.g. a NAS share mounted into
    the container), or with `device` a folder on that device over SFTP (its `ssh:` login)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(pattern=r"^/[^\x00\n]*$", max_length=500)
    device: str | None = None

    @model_validator(mode="after")
    def _plain(self) -> Self:
        if ".." in self.path.split("/"):
            raise ValueError("a backup target's path has no ..")
        return self


class HubBackupSettings(BaseModel):
    """Kastellan's own backup (hubbackup.py)."""

    model_config = ConfigDict(extra="forbid")

    every_hours: int = Field(default=24, ge=1, le=24 * 30)
    keep: int = Field(default=30, ge=1, le=1000)
    history: bool = True  # check and vitals history: larger, but the graphs come back too
    logs: bool = False  # the central logs (logs.db): large; they come back in by themselves
    targets: list[HubBackupTarget] = Field(default_factory=list, max_length=8)


class DeviceBackupSettings(BaseModel):
    """Device configuration backups (devbackups.py): taken from every device whose login may
    (OpenWrt's `backup` ability, a FRITZ!Box login), deduplicated, kept encrypted."""

    model_config = ConfigDict(extra="forbid")

    every_hours: int = Field(default=24, ge=1, le=24 * 30)
    keep: int = Field(default=60, ge=1, le=1000)  # different versions kept per device


class SpeedTest(BaseModel):
    """Measured speed tests (internet.py): off by default, they use the line."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    every_hours: int = Field(default=24, ge=1, le=24 * 30)
    download: str = Field(
        default="https://speed.cloudflare.com/__down?bytes=50000000", pattern=r"^https?://\S+$"
    )
    upload: str | None = Field(default="https://speed.cloudflare.com/__up", pattern=r"^https?://\S+$")
    upload_bytes: int = Field(default=10_000_000, ge=1_000_000, le=200_000_000)
    # An iperf3 server ("host" or "host:port") to measure against instead of the HTTP transfers:
    # one of your own outside, or a public one. Needs iperf3 where the hub runs (the image has it).
    iperf3: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9.-]+(:\d{1,5})?$|^\[[0-9A-Fa-f:]+\](:\d{1,5})?$"
    )
    # What the contract says (Mbit/s): a test far below for a while raises a warning.
    booked_down: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    booked_up: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class InternetSettings(BaseModel):
    """The internet connection (internet.py): checked from the hub every `every` seconds."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    every: int = Field(default=30, ge=10, le=600)
    # Outside addresses to ping, from more than one provider (one's trouble isn't an outage).
    targets: list[str] = Field(
        default_factory=lambda: ["1.1.1.1", "9.9.9.9", "8.8.8.8"], min_length=1, max_length=8
    )
    targets_v6: list[str] = Field(
        default_factory=lambda: ["2606:4700:4700::1111", "2620:fe::fe"], max_length=8
    )
    dns: list[str] = Field(default_factory=lambda: ["example.com", "wikipedia.org"], max_length=8)
    # The device that connects to the internet (its line, its counters); None: the router whose
    # address is the hub's gateway, or the only FRITZ!Box / OpenWrt router.
    router: str | None = None
    speedtest: SpeedTest = Field(default_factory=SpeedTest)


class WifiSettings(BaseModel):
    """The Wi-Fi page (wifi.py): a daily scan at a quiet hour (local "HH:MM"; None: only on
    request), by every OpenWrt radio whose login may scan."""

    model_config = ConfigDict(extra="forbid")

    scan_at: str | None = Field(default="04:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


SECRET_REF = re.compile(r"^secret:([a-z0-9][a-z0-9_.-]{0,63})$")
URL_OR_SECRET = r"^(https?://[^\s]+|secret:[a-z0-9][a-z0-9_.-]{0,63})$"


class NotifyChannel(BaseModel):
    """Where alarms go (notify.py): exactly one of ntfy, webhook, gotify, telegram, email or push."""

    model_config = ConfigDict(extra="forbid")

    # Any of the URLs, the token and the SMTP login may be `secret:<name>`: a secret Kastellan
    # keeps (the key ring), so config.yaml holds no credentials.
    ntfy: str | None = Field(default=None, pattern=URL_OR_SECRET)
    webhook: str | None = Field(default=None, pattern=URL_OR_SECRET)
    gotify: str | None = Field(default=None, pattern=URL_OR_SECRET)  # the server
    telegram: str | int | None = None  # the chat id (a bot's token in `token`)
    email: str | None = Field(default=None, pattern=r"^[^@\s,]+@[^@\s,]+$")  # to whom
    push: str | None = Field(
        default=None, pattern=r"^(\*|[\w.-]{1,64})$"
    )  # a user's browsers (push.py), * all
    # smtp://user:password@host:587 (STARTTLS) or smtps://...:465 (TLS); for email.
    smtp: str | None = Field(default=None, pattern=r"^(smtps?://[^\s]+|secret:[a-z0-9][a-z0-9_.-]{0,63})$")
    sender: str | None = Field(default=None, pattern=r"^[^@\s,]+@[^@\s,]+$")  # email: From
    token: str | None = None  # ntfy access token, Gotify app token, Telegram bot token
    min: Literal["info", "warning", "critical"] = "warning"
    quiet: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")  # only critical then

    KINDS: ClassVar[tuple[str, ...]] = ("ntfy", "webhook", "gotify", "telegram", "email", "push")

    @property
    def kind(self) -> str:
        return next(k for k in self.KINDS if getattr(self, k) is not None)

    def secret_refs(self) -> dict[str, str]:
        """field -> the secret it names (`secret:<name>`)."""
        return {
            f: m.group(1)
            for f in ("ntfy", "webhook", "gotify", "smtp", "token")
            if isinstance(getattr(self, f), str) and (m := SECRET_REF.match(getattr(self, f)))
        }

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if sum(getattr(self, k) is not None for k in self.KINDS) != 1:
            raise ValueError(
                "a notify channel is one of ntfy:, webhook:, gotify:, telegram:, email: or push:"
            )
        if self.gotify is not None and not self.token:
            raise ValueError("gotify: needs token: (an application token from Gotify)")
        if self.telegram is not None:
            if not self.token or not (re.match(r"^\d+:[\w-]+$", self.token) or SECRET_REF.match(self.token)):
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

    kind: Literal["wireguard"] = "wireguard"
    name: str = Field(default="", max_length=60)  # shown; the id when empty
    wg_easy: str = Field(pattern=r"^https?://[^\s]+$")  # wg-easy's address, e.g. http://sv1:51821
    user: str = "admin"
    # Its login's password, a secret (the key ring, or data/secrets/<name>); one per VPN made in
    # the app (wg-easy-<id>), "wg-easy" for the one from before there could be several.
    password_secret: str = Field(default="wg-easy", pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class OpenVpnSource(BaseModel):
    """OpenVPN clients through an easy-rsa PKI (vpn/openvpn.py).

    mode restricted: the hub's VPN key may only run kastellan-pki / kastellan-crl on the hosts
    (installed there; `pki_user` and `server_user` are those restricted logins).
    mode direct: nothing is installed; the same scripts are sent over the devices' normal
    `ssh:` logins on every call."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["openvpn"] = "openvpn"
    name: str = Field(default="", max_length=60)  # shown; the id when empty
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
    # Which of the server device's OpenVPN servers this is (a uci section, or a file in
    # /etc/openvpn), for who is connected; needed only when the device runs several.
    instance: str | None = Field(default=None, pattern=r"^[\w.-]{1,64}$")

    @model_validator(mode="after")
    def _reserved_names(self) -> Self:
        for name in self.reserved:
            if not re.match(r"^[a-z0-9][a-z0-9_.-]{0,63}$", name):
                raise ValueError(f"reserved: {name!r} is not a certificate name")
        return self


# One VPN Kastellan manages the clients of; `vpns:` has any number, of either kind.
Vpn = Annotated[WireGuardSource | OpenVpnSource, Field(discriminator="kind")]
VPN_NAMES = {"wireguard": "WireGuard", "openvpn": "OpenVPN"}


class GroupSettings(BaseModel):
    """Devices that belong together (any kind). For its OpenWrt members, settings are kept the
    same (openwrt/sync.py): Wi-Fi networks by name, the mesh, root's password."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    devices: list[str] = Field(min_length=1, max_length=64)


DOMAIN = r"^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]*[a-z0-9])?$"


class WebSettings(BaseModel):
    """Device web pages through the hub (webproxy.py): the hub answers at `domain`, and a device
    at `<device id>.<domain>` (one wildcard DNS entry for both). TLS ends at a proxy in front
    (nginx-proxy, Let's Encrypt via DNS-01 into the site's zone); web_setup.py checks names,
    certificates and the routers' DNS."""

    model_config = ConfigDict(extra="forbid")

    domain: str = Field(max_length=200, pattern=DOMAIN)
    # The site's DNS zone (default: `domain` without its first label) and its DNS server, which
    # the routers forward the zone to (and allow private answers from: rebind protection).
    zone: str | None = Field(default=None, max_length=200, pattern=DOMAIN)
    dns: str | None = Field(default=None, max_length=45)

    @property
    def the_zone(self) -> str:
        return self.zone or (self.domain.split(".", 1)[1] if "." in self.domain else self.domain)

    @model_validator(mode="after")
    def _dns_is_an_address(self) -> Self:
        if self.dns is not None:
            try:
                ipaddress.ip_address(self.dns)
            except ValueError as e:
                raise ValueError(f"web.dns: {self.dns!r} is not an address") from e
        return self


class RingSettings(BaseModel):
    """One key ring's choices (vault.py); what is left out comes from the level."""

    model_config = ConfigDict(extra="forbid")

    # Ways it opens, besides its recovery key: `password` (an admin signing in), `config` (`key`
    # below), `{keyfile: /path}`, `{env: VARIABLE}`.
    unlock: list[str | dict[str, str]] | None = None
    stays_unlocked: str | None = Field(default=None, pattern=r"^(restart|lock|signed_in|idle:\d{1,4})$")
    background: bool | None = None  # may Kastellan use its entries by itself (never `people`)
    # The ring's key itself, for `unlock: [config]`: whoever has a copy of config.yaml opens it.
    key: str | None = Field(default=None, min_length=20, max_length=200)

    @model_validator(mode="after")
    def _ways(self) -> Self:
        for item in self.unlock or []:
            if isinstance(item, str):
                if item not in ("password", "config"):
                    raise ValueError(
                        f"unlock: {item!r}: password, config, {{keyfile: /path}} or {{env: NAME}}"
                    )
                continue
            if len(item) != 1 or next(iter(item)) not in ("keyfile", "env"):
                raise ValueError("unlock: {keyfile: /path} or {env: NAME}, one per item")
            way, source = next(iter(item.items()))
            if way == "keyfile" and not re.match(r"^/[\w./-]+$", source):
                raise ValueError(f"unlock: keyfile {source!r} isn't an absolute path")
            if way == "env" and not re.match(r"^[A-Z_][A-Z0-9_]{0,63}$", source):
                raise ValueError(f"unlock: env {source!r} isn't a variable name")
        if "config" in (self.unlock or []) and not self.key:
            raise ValueError("unlock: config needs the ring's key (made under Settings → Keys)")
        return self


class VaultSettings(BaseModel):
    """Key rings (vault.py): how secure they are. A level fills in what a ring leaves out:
    convenient (Kastellan's ring opens by itself at start), balanced, strict."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["convenient", "balanced", "strict"] = "balanced"
    rings: dict[str, RingSettings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ids(self) -> Self:
        for rid in self.rings:
            if not re.match(r"^[a-z0-9][a-z0-9_-]{0,39}$", rid):
                raise ValueError(f"vault: ring {rid!r}: lowercase letters, digits, _ and -")
        return self


class BackupCheck(BaseModel):
    """A backup that tells the hub when it ran (backups.py): its job calls
    `POST /api/backups/<id>/ok` (or `/fail`) with its token; no word within `within_hours` (or a
    failure since the last success) is an alarm on `device`.

        backups:
          nas-restic:
            name: NAS to B2
            device: nas
            within_hours: 26
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=100)
    device: str
    within_hours: float = Field(default=26, gt=0, le=24 * 62)


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class MaintenanceWindow(BaseModel):
    """Planned work (maintenance.py): the problems of these devices show, marked, and are sent
    only if still there when it ends. Once (`start`, `end`) or weekly (`days`, `at`, `minutes`),
    in the hub's local time.

        maintenance:
          - name: Patch night
            groups: [servers]
            days: [sun]
            at: "03:00"
            minutes: 90
          - devices: [nas]
            start: 2026-11-28 20:00
            end: 2026-11-28 23:00
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=100)
    devices: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    sites: list[str] = Field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(default_factory=list)
    at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 7)

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        once = self.start is not None or self.end is not None
        weekly = bool(self.days) or self.at is not None or self.minutes is not None
        if once == weekly:
            raise ValueError("maintenance: either start and end, or days, at and minutes")
        if once:
            if self.start is None or self.end is None:
                raise ValueError("maintenance: start and end, both")
            self.start, self.end = _aware(self.start), _aware(self.end)
            if self.end <= self.start:
                raise ValueError("maintenance: an end after its start")
        if weekly and not (self.days and self.at and self.minutes):
            raise ValueError("maintenance: days, at and minutes, all three")
        if not (self.devices or self.groups or self.sites):
            raise ValueError("maintenance: which devices, groups or sites")
        return self


def _aware(t: datetime) -> datetime:
    """A time without a zone is the hub's local time."""
    return t if t.tzinfo else t.astimezone()


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
    vpns: dict[str, Vpn] = Field(default_factory=dict)
    # A URL Kastellan calls every minute (a dead man's switch, e.g. healthchecks.io): when the
    # calls stop, that service tells you the hub (or its host, or the internet line) is gone.
    heartbeat: str | None = Field(default=None, pattern=r"^https://[^\s]+$")
    # Ask GitHub once a day whether a newer Kastellan is out (releases.py).
    check_releases: bool = True
    networks: dict[str, NetworkSettings] = Field(default_factory=dict)
    groups: dict[str, GroupSettings] = Field(default_factory=dict)
    # Names for the sites devices are at (`site:` of a device), e.g. hetzner: Hetzner Cloud.
    sites: dict[str, str] = Field(default_factory=dict)
    web: WebSettings | None = None  # device web pages through the hub, on their own names
    vault: VaultSettings = Field(default_factory=VaultSettings)  # key rings: how they open
    maintenance: list[MaintenanceWindow] = Field(default_factory=list)  # planned work
    backups: dict[str, BackupCheck] = Field(default_factory=dict)  # backups that report in
    logs: LogSettings = Field(default_factory=LogSettings)  # central logs
    device_backups: DeviceBackupSettings = Field(default_factory=DeviceBackupSettings)
    hub_backup: HubBackupSettings = Field(default_factory=HubBackupSettings)
    internet: InternetSettings = Field(default_factory=InternetSettings)
    wifi: WifiSettings = Field(default_factory=WifiSettings)

    def site_of(self, id: str) -> str:
        return self.devices[id].site or HOME

    def site_name(self, site: str) -> str:
        return self.sites.get(site) or site.replace("-", " ").replace("_", " ").capitalize()

    def label(self, id: str) -> str:
        """A device for alarms and notifications: its name, and where it is when that isn't home."""
        site = self.site_of(id)
        name = self.devices[id].name
        return name if site == HOME else f"{name} ({self.site_name(site)})"

    @model_validator(mode="after")
    def _groups(self) -> Self:
        for gid, group in self.groups.items():
            if not ID.fullmatch(gid):
                raise ValueError(f"group id {gid!r}: use lowercase letters, digits, - and _")
            for id in group.devices:
                if id not in self.devices:
                    raise ValueError(f"groups.{gid}: {id!r} is not a device")
            if len(set(group.devices)) != len(group.devices):
                raise ValueError(f"groups.{gid}: a device is listed twice")
            group.name = group.name or gid
        return self

    @model_validator(mode="after")
    def _hub_backup(self) -> Self:
        seen = set()
        for t in self.hub_backup.targets:
            identity = (t.device, t.path.rstrip("/") or "/")
            if identity in seen:
                raise ValueError("hub_backup.targets: duplicate destination")
            seen.add(identity)
            if t.device is not None and (t.device not in self.devices or self.devices[t.device].ssh is None):
                raise ValueError("hub_backup.targets: device needs an SSH login")
        return self

    @model_validator(mode="after")
    def _internet(self) -> Self:
        if self.internet.router is not None and self.internet.router not in self.devices:
            raise ValueError(f"internet.router: no {self.internet.router!r}")
        for t in [*self.internet.targets, *self.internet.targets_v6]:
            try:
                ipaddress.ip_address(t)
            except ValueError as e:
                raise ValueError(f"internet targets are addresses: {t!r}") from e
        return self

    @model_validator(mode="after")
    def _device_secret_names(self) -> Self:
        accesses = [a for d in self.devices.values() for a in (d.openwrt, d.fritzbox) if a]
        accesses += [v for v in self.vpns.values() if v.kind == "wireguard"]
        names = {access.password_secret for access in accesses}
        names.update(name for channel in self.notify for name in channel.secret_refs().values())
        for name in names:
            if name in OWN:
                raise ValueError(
                    "a login or notification cannot use Kastellan's encryption or fingerprint key"
                )
        return self

    @model_validator(mode="after")
    def _backups(self) -> Self:
        for bid, b in self.backups.items():
            if not ID.fullmatch(bid) or len(bid) > 64:
                raise ValueError(f"backups: {bid!r}: ids are lowercase letters, digits, - and _")
            if b.device not in self.devices:
                raise ValueError(f"backups.{bid}.device: no {b.device!r}")
            b.name = b.name or bid
        return self

    @model_validator(mode="after")
    def _maintenance(self) -> Self:
        for i, w in enumerate(self.maintenance):
            for kind, ids, known in (
                ("devices", w.devices, self.devices),
                ("groups", w.groups, self.groups),
                ("sites", w.sites, {HOME, *self.sites, *(d.site for d in self.devices.values())}),
            ):
                for x in ids:
                    if x not in known:
                        raise ValueError(f"maintenance[{i}].{kind}: no {x!r}")
        return self

    @model_validator(mode="after")
    def _site_ids(self) -> Self:
        for site in self.sites:
            if not ID.fullmatch(site):
                raise ValueError(f"site id {site!r}: use lowercase letters, digits, - and _")
        return self

    @model_validator(mode="after")
    def _network_ids(self) -> Self:
        if "internet" in self.networks:
            raise ValueError("networks: internet is built in; give your network another id")
        return self

    @model_validator(mode="before")
    @classmethod
    def _old_vpn(cls, data: Any) -> Any:
        """`vpn: {wireguard: ..., openvpn: ...}` (one of each, before 0.8) reads as `vpns:` with
        those two ids; the app writes `vpns:` when it changes them."""
        if not isinstance(data, dict) or "vpn" not in data:
            return data
        data = dict(data)
        old = data.pop("vpn") or {}
        if not isinstance(old, dict):
            raise ValueError("vpn: is wireguard: and openvpn: (or use vpns:)")
        if data.get("vpns") and any(old.values()):
            raise ValueError("vpn: and vpns: both: move vpn: into vpns:")
        vpns = dict(data.get("vpns") or {})
        for kind in ("wireguard", "openvpn"):
            if isinstance(old.get(kind), dict):
                vpns[kind] = {"kind": kind, "name": VPN_NAMES[kind], **old[kind]}
        data["vpns"] = vpns
        return data

    @model_validator(mode="after")
    def _vpns(self) -> Self:
        used: dict[tuple[str, str], str] = {}  # (device, restricted login) -> the VPN using it
        for vid, vpn in self.vpns.items():
            if not ID.fullmatch(vid) or len(vid) > 40:
                raise ValueError(f"vpns: {vid!r}: ids are lowercase letters, digits, - and _")
            if vpn.kind != "openvpn":
                continue
            roles = [("pki", vpn.pki, vpn.pki_user)]
            if vpn.server:
                roles.append(("server", vpn.server, vpn.server_user))
            for role, id, user in roles:
                if id not in self.devices:
                    raise ValueError(f"vpns.{vid}.{role}: {id!r} is not a device")
                if vpn.mode == "direct" and self.devices[id].ssh is None:
                    raise ValueError(f"vpns.{vid}.{role}: {id!r} needs ssh: (its login) in direct mode")
                if vpn.mode != "restricted":
                    continue
                # One VPN key, bound to one script per login: two VPNs can't share a login there.
                other = used.get((id, user), vid)
                if other != vid:
                    raise ValueError(f"vpns.{vid}.{role}: {user} on {id} is {other}'s; give it its own user")
                used[(id, user)] = vid
        return self

    def vpn_name(self, vid: str) -> str:
        vpn = self.vpns[vid]
        return vpn.name or vid

    @model_validator(mode="after")
    def _ids_and_parents(self) -> Self:
        for id, device in self.devices.items():
            if not ID.fullmatch(id):
                raise ValueError(f"device id {id!r}: use lowercase letters, digits, - and _")
            if not device.name:
                device.name = id
            for net in device.network:
                if net != "internet" and net not in self.networks:
                    raise ValueError(f"device {id!r}: network {net!r} is not in networks: (or internet)")
            if device.site is not None and not ID.fullmatch(device.site):
                raise ValueError(
                    f"device {id!r}: site {device.site!r}: use lowercase letters, digits, - and _"
                )
            if device.parent is not None and device.parent not in self.devices:
                raise ValueError(f"device {id!r}: parent {device.parent!r} is not a device")
            if device.ups is not None:
                if device.ups.via == id:
                    raise ValueError(f"device {id!r}: ups.via: the host that reads it, not itself")
                if device.ups.via not in self.devices:
                    raise ValueError(f"device {id!r}: ups.via {device.ups.via!r} is not a device")
                if not self.devices[device.ups.via].host:
                    raise ValueError(f"device {id!r}: ups.via needs a host with an address")
            if device.powered_by is not None:
                ups = self.devices.get(device.powered_by)
                if ups is None or ups.kind is not Kind.UPS:
                    raise ValueError(f"device {id!r}: powered_by {device.powered_by!r} is not a UPS device")
                seen, at = {id}, device.powered_by
                while at is not None:  # a UPS on a UPS is fine; a circle isn't
                    if at in seen:
                        raise ValueError(f"device {id!r}: powered_by goes round in a circle")
                    seen.add(at)
                    at = self.devices[at].powered_by if at in self.devices else None
            if device.runs_on is not None:
                if device.runs_on.host == id:
                    raise ValueError(f"device {id!r}: runs_on: a device can't run on itself")
                if device.runs_on.host not in self.devices:
                    raise ValueError(f"device {id!r}: runs_on.host {device.runs_on.host!r} is not a device")
        # Each relation is acyclic on its own. Together they may cycle legitimately: a
        # virtual router can run on the server whose network parent is that router.
        for relation in ("parent", "runs_on"):
            dependencies = {}
            for id, d in self.devices.items():
                parent = d.parent if relation == "parent" else d.runs_on.host if d.runs_on else None
                dependencies[id] = [parent] if parent is not None else []
            try:
                TopologicalSorter(dependencies).prepare()
            except CycleError as e:
                raise ValueError(f"{relation}: device dependencies go round in a circle") from e
        return self


def parse(text: str) -> Config:
    """A config.yaml's text; an empty one is an empty configuration."""
    return Config.model_validate(YAML(typ="safe").load(text) or {})


def load(path: Path) -> Config:
    """Read config.yaml; a missing or empty file is an empty configuration."""
    return parse(path.read_text()) if path.exists() else Config()
