"""Sign-ins and attempts across the network, read from the central logs and the hub's audit log:
SSH (OpenSSH, dropbear), OpenVPN, WireGuard presence, FRITZ!Box logins, the hub's own sign-ins.

Each line becomes an event: when, where (the device and its service), who (the account, if the
line names one), from which address, and the outcome (`ok`, `failed`, or `attempt` when a
connection never got as far as a login). Events are then summed up per address, with flags:
- `repeated`: many failures from one address (a password guesser, or a broken client);
- `new-outside`: a success from an outside address not seen succeeding before;
- `outside-ok`: any success from an outside address.

"Paths" are suggestions, not identities: a login from an address that a VPN gave one of its
clients is shown as coming through that client. Address lookups stay local (private or public,
the VPN addresses Kastellan knows); nothing is sent anywhere."""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from .networks import is_public

# The words a line must contain to be read at all (SQL LIKE, for LogStore.matching).
LIKES = [
    "%Accepted %",
    "%Failed password%",
    "%Failed publickey%",
    "%Failed none%",
    "%Failed keyboard-interactive%",
    "%Invalid user%",
    "%authenticating user%",
    "%invalid user%",
    "%auth succeeded%",
    "%Bad password attempt%",
    "%nonexistent user%",
    "%Exit before auth%",
    "%Connection closed by%",
    "%Connection reset by%",
    "%Disconnected from%",
    "%TCP connection established%",
    "%Peer Connection Initiated%",
    "%TLS Error%",
    "%TLS handshake failed%",
    "%VERIFY ERROR%",
    "%AUTH_FAILED%",
    "%connected (handshake%",
    "%FRITZ!Box%",
    "%Anmeldung%",
]

_ADDR = r"(?<![\w.])(?P<addr>[0-9a-fA-F:.]+?)"
_PORT = r"(?:[ :]|\s+port\s+)\d+"
_FROM_IP = r"(?:IP address|IP-Adresse):? \(?(?P<addr>[0-9a-fA-F:.]*[0-9a-fA-F])(?![\w:])"
RULES: list[tuple[str, str, re.Pattern[str]]] = [
    # OpenSSH
    ("ssh", "ok", re.compile(rf"Accepted (?P<how>\S+) for (?P<user>\S+) from {_ADDR} port \d+")),
    (
        "ssh",
        "failed",
        re.compile(
            rf"Failed (?P<how>password|publickey|none|keyboard-interactive\S*) for "
            rf"(?:invalid user )?(?P<user>\S+) from {_ADDR} port \d+"
        ),
    ),
    ("ssh", "failed", re.compile(rf"Invalid user (?P<user>\S*) from {_ADDR} port \d+")),
    (
        "ssh",
        "attempt",
        re.compile(
            r"(?:Connection closed by|Connection reset by|Disconnected from) (?:authenticating |invalid )?"
            rf"user (?P<user>\S+) {_ADDR} port \d+ \[preauth\]"
        ),
    ),
    (
        "ssh",
        "attempt",
        re.compile(rf"(?:Connection closed by|Connection reset by) {_ADDR} port \d+ \[preauth\]"),
    ),
    ("ssh", "attempt", re.compile(rf"Exit before auth from <{_ADDR}:\d+>")),
    # dropbear
    (
        "ssh",
        "ok",
        re.compile(rf"(?P<how>Password|Pubkey) auth succeeded for '(?P<user>[^']*)'.*? from {_ADDR}{_PORT}"),
    ),
    ("ssh", "failed", re.compile(rf"Bad password attempt for '(?P<user>[^']*)' from {_ADDR}{_PORT}")),
    (
        "ssh",
        "failed",
        re.compile(rf"Login attempt for nonexistent user (?:'(?P<user>[^']*)' )?from {_ADDR}{_PORT}"),
    ),
    # OpenVPN: "1.2.3.4:5678 [alice] Peer Connection Initiated with [AF_INET]1.2.3.4:5678"
    (
        "vpn",
        "ok",
        re.compile(rf"{_ADDR}:\d+ \[(?P<user>[^\]]+)\] Peer Connection Initiated"),
    ),
    ("vpn", "ok", re.compile(rf"(?P<user>[^\s/:]+)/{_ADDR}:\d+ Peer Connection Initiated")),
    (
        "vpn",
        "failed",
        re.compile(rf"{_ADDR}:\d+ (?:TLS Error|TLS handshake failed|VERIFY ERROR|.*AUTH_FAILED)"),
    ),
    ("vpn", "attempt", re.compile(rf"TCP connection established with \[AF_INET6?\]{_ADDR}:\d+")),
    # WireGuard presence (logs_poll.wireguard): no address from outside, the client's own.
    ("vpn", "ok", re.compile(r"^(?P<user>.+?) connected \(handshake; (?P<inner>[0-9a-fA-F:./]+)\)")),
    # FRITZ!Box (English and German event log)
    (
        "router",
        "failed",
        re.compile(
            rf"(?:Login|Anmeldung).*?(?:failed|fehlgeschlagen|ungültig).*?{_FROM_IP}",
            re.I,
        ),
    ),
    (
        "router",
        "ok",
        re.compile(
            r"^(?!.*(?:failed|fehlgeschlagen)).*?(?:User|Benutzer\S*) (?P<user>\S+) "
            rf"(?:logged in|hat sich).*?{_FROM_IP}",
            re.I,
        ),
    ),
    (
        "router",
        "ok",
        re.compile(
            rf"^(?!.*fehlgeschlagen)Anmeldung .*?Benutzers? (?P<user>\S+) an der FRITZ!Box.*?{_FROM_IP}"
        ),
    ),
]


@dataclass
class Event:
    ts: float
    device: str
    service: str  # ssh, vpn, router, hub
    program: str
    outcome: str  # ok, failed, attempt
    user: str | None
    address: str | None  # where it came from
    outside: bool  # from a public address
    via: str | None = None  # a VPN client that address belongs to (a suggested path)
    line: int | None = None  # the log line's id (links to it on the Logs page)
    verified: bool = True  # false for unauthenticated syslog; never evidence for a success alert


def _address(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip("[]").removeprefix("::ffff:")
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def parse(line: dict[str, Any]) -> Event | None:
    """One log line as a sign-in event, or None."""
    msg = str(line.get("msg") or "")
    program = str(line.get("program") or "")
    for service, outcome, rule in RULES:
        m = rule.search(msg)
        if not m:
            continue
        groups = m.groupdict()
        if groups.get("inner") is not None:  # WireGuard: the client's address inside the VPN
            inner = _address(groups["inner"].split("/")[0])
            return Event(
                ts=float(line["ts"]),
                device=str(line.get("device") or ""),
                service="vpn",
                program=program,
                outcome="ok",
                user=(groups.get("user") or "")[:64] or None,
                address=None,
                outside=False,
                via=inner,
                line=line.get("id"),
                verified=bool(line.get("verified", False)),
            )
        address = _address(groups.get("addr"))
        if address is None:
            continue
        user = (groups.get("user") or "")[:64] or None
        return Event(
            ts=float(line["ts"]),
            device=str(line.get("device") or ""),
            service=service,
            program=program,
            outcome=outcome,
            user=user,
            address=address,
            outside=is_public(address),
            line=line.get("id"),
            verified=bool(line.get("verified", False)),
        )
    return None


def from_audit(entries: list[dict[str, Any]], since: float) -> list[Event]:
    """The hub's own sign-ins (audit log: signin, signin.failed, signin.blocked)."""
    from datetime import datetime

    out = []
    for e in entries:
        action = e.get("action")
        if action not in ("signin", "signin.failed", "signin.blocked"):
            continue
        try:
            ts = datetime.fromisoformat(str(e.get("time"))).timestamp()
        except ValueError:
            continue
        if ts < since:
            continue
        address = _address(e.get("address"))
        out.append(
            Event(
                ts=ts,
                device="kastellan",
                service="hub",
                program="kastellan",
                outcome="ok" if action == "signin" else "failed",
                user=(str(e.get("user") or "")[:64] or None),
                address=address,
                outside=is_public(address),
            )
        )
    return out


def mark_paths(events: list[Event], vpn_addresses: dict[str, str]) -> None:
    """A login from an address a VPN gave a client: through that client (a suggestion)."""
    for e in events:
        if e.via is None and e.address and e.address in vpn_addresses:
            e.via = vpn_addresses[e.address]


@dataclass
class Source:
    address: str
    outside: bool
    ok: int = 0
    failed: int = 0
    attempts: int = 0
    first: float = 0.0
    last: float = 0.0
    users: set[str] = field(default_factory=set)
    services: set[str] = field(default_factory=set)
    devices: set[str] = field(default_factory=set)
    via: str | None = None
    flags: list[str] = field(default_factory=list)

    def view(self) -> dict[str, Any]:
        d = asdict(self)
        d["users"] = sorted(self.users)[:20]
        d["services"] = sorted(self.services)
        d["devices"] = sorted(self.devices)
        return d


REPEATED = 10  # failures (and attempts) from one address: flagged


def sources(events: list[Event], known_ok: set[str]) -> list[Source]:
    """Per address: how many of each outcome, who, where; flagged. `known_ok`: outside addresses
    that succeeded before (kept by the hub), so a first success from one is `new-outside`."""
    by: dict[str, Source] = {}
    confirmed_ok = {e.address for e in events if e.verified and e.outcome == "ok"}
    for e in events:
        if not e.address:
            continue
        s = by.get(e.address)
        if s is None:
            s = by[e.address] = Source(e.address, e.outside, first=e.ts, last=e.ts)
        s.first, s.last = min(s.first, e.ts), max(s.last, e.ts)
        if e.outcome == "ok":
            s.ok += 1
        elif e.outcome == "failed":
            s.failed += 1
        else:
            s.attempts += 1
        if e.user:
            s.users.add(e.user)
        s.services.add(e.service)
        s.devices.add(e.device)
        s.via = s.via or e.via
    for s in by.values():
        if s.failed + s.attempts >= REPEATED:
            s.flags.append("repeated")
        if s.outside and s.address in confirmed_ok:
            s.flags.append("outside-ok")
            if s.address not in known_ok:
                s.flags.append("new-outside")
    return sorted(by.values(), key=lambda s: (-len(s.flags), -(s.failed + s.attempts + s.ok), s.address))


def summary(events: list[Event]) -> dict[str, Any]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "failed": 0, "attempt": 0})
    for e in events:
        counts[e.service][e.outcome] += 1
    outside = {e.address for e in events if e.outside and e.address}
    return {
        "by_service": dict(counts),
        "outside_addresses": len(outside),
        "outside_ok": sum(1 for e in events if e.outside and e.outcome == "ok"),
    }
