"""DNS and HTTPS for a site (PLAN: "DNS + HTTPS for a site"): the site has its own zone, a
proxy in front of the hub ends TLS with Let's Encrypt certificates (DNS-01 into the zone), the
hub is `web.domain` and device pages are `*.web.domain`. Kastellan doesn't terminate TLS itself;
it checks that the pieces fit:

- names: `web.domain`, a made-up name under it (the wildcard) and a device's page resolve, and
  the same through the hub's resolver as through a public one (never split DNS);
- certificates: the proxy's, for the hub's name and for a device page's name (a challenge
  record below `web.domain` stops a plain zone wildcard from covering it), and how long they last;
- the OpenWrt routers' DNS (their read login may read `dhcp`): the zone allowed past rebind
  protection and forwarded to the site's DNS server (`web.dns`), so names resolve offline too.

Checked every few hours (and on request); a certificate near its end, or names that stopped
resolving, raise warnings.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import dns.asyncresolver
import dns.exception
import dns.resolver
from fastapi import APIRouter, Request

from . import tls
from .alarms import Problem, Severity
from .auth_api import require_admin
from .webproxy import label

if TYPE_CHECKING:
    from .hub import Hub

EVERY = 6 * 3600
CERT_DAYS = 14  # a proxy certificate with fewer days left: a warning
PUBLIC_RESOLVER = "1.1.1.1"


async def resolve(name: str, nameserver: str | None = None) -> list[str]:
    """A and AAAA answers (sorted), through the system's resolvers or `nameserver`."""
    r = dns.asyncresolver.Resolver(configure=nameserver is None)
    if nameserver:
        r.nameservers = [nameserver]
    r.lifetime = 4
    out: set[str] = set()
    for kind in ("A", "AAAA"):
        try:
            answer = await r.resolve(name, kind)
            out |= {a.to_text() for a in answer}
        except (dns.exception.DNSException, OSError):
            continue
    return sorted(out)


def router_dns(values: dict[str, Any], zone: str, server: str | None) -> dict[str, Any]:
    """From an OpenWrt router's `dhcp` config: whether its dnsmasq lets the zone's private
    answers through (rebind_domain) and forwards the zone to the site's DNS server."""
    masq = next((v for v in values.values() if isinstance(v, dict) and v.get(".type") == "dnsmasq"), None)
    if masq is None:
        return {"dnsmasq": False, "rebind_ok": None, "forward_ok": None, "forwards": []}

    def as_list(v: Any) -> list[str]:
        return [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []

    rebind_on = str(masq.get("rebind_protection", "1")) != "0"
    domains = as_list(masq.get("rebind_domain"))
    forwards = [s for s in as_list(masq.get("server")) if s.startswith(f"/{zone}/")]
    return {
        "dnsmasq": True,
        "rebind_ok": not rebind_on or zone in [d.strip("/") for d in domains],
        "forward_ok": bool(forwards) and (server is None or any(f.endswith(f"/{server}") for f in forwards)),
        "forwards": forwards,
        "local": [a for a in as_list(masq.get("address")) if f"/{zone}/" in a or a.startswith(f"/{zone}")],
    }


def commands(zone: str, server: str | None) -> str:
    """The OpenWrt lines for the zone: allowed past rebind protection, forwarded to the site's DNS."""
    s = server or "<the site's DNS server>"
    return (
        f"uci add_list dhcp.@dnsmasq[0].rebind_domain='{zone}'\n"
        f"uci add_list dhcp.@dnsmasq[0].server='/{zone}/{s}'\n"
        "uci commit dhcp && /etc/init.d/dnsmasq restart"
    )


async def look(hub: Hub) -> dict[str, Any]:
    """Everything the page shows, checked now."""
    web = hub.config.web
    now = datetime.now(UTC)
    if web is None:
        return {"domain": None, "checked": now.isoformat(timespec="seconds")}
    zone, domain = web.the_zone, web.domain
    device = next(iter(hub.config.devices), None)
    device_name = f"{label(device)}.{domain}" if device else None
    names = [
        (domain, "the hub"),
        (f"kastellan-check-{secrets.token_hex(3)}.{domain}", "any name under it (wildcard)"),
        *([(device_name, f"a device's page ({device})")] if device_name else []),
    ]

    async def one_name(name: str, what: str) -> dict[str, Any]:
        local, public = await asyncio.gather(resolve(name), resolve(name, PUBLIC_RESOLVER))
        return {
            "name": name,
            "what": what,
            "answers": local,
            "public": public,
            "same": local == public if public else None,
            "ok": bool(local),
        }

    resolved = await asyncio.gather(*(one_name(n, w) for n, w in names))
    certs = []
    for name in (domain, device_name):
        if not name:
            continue
        address = next((r["answers"][0] for r in resolved if r["name"] == name and r["answers"]), None)
        cert = await tls.certificate(address or name, 443, name, 8) if address else None
        certs.append({"name": name, "certificate": cert.view(now) if cert else None})
    routers = []
    for id, d in hub.config.devices.items():
        if d.openwrt is None:
            continue
        try:
            values = (await hub.ubus(id).call("uci", "get", {"config": "dhcp"})).get("values", {})
            routers.append({"device": id, **router_dns(values, zone, web.dns)})
        except Exception as e:  # not readable (an older login), or it doesn't answer
            routers.append({"device": id, "error": str(e)[:160]})
    return {
        "domain": domain,
        "configuration": web.model_dump(mode="json"),
        "zone": zone,
        "dns_server": web.dns,
        "address": hub.own_address,
        "names": resolved,
        "certificates": certs,
        "routers": routers,
        "commands": commands(zone, web.dns),
        "checked": now.isoformat(timespec="seconds"),
    }


def problems(hub: Hub) -> list[Problem]:
    state = hub.web_state
    if not state or hub.config.web is None or state.get("domain") != hub.config.web.domain:
        return []
    out = []
    for c in state.get("certificates") or []:
        cert = c.get("certificate")
        invalid = cert and cert.get("valid") is False
        if cert and (invalid or (cert.get("days_left") is not None and cert["days_left"] < CERT_DAYS)):
            out.append(
                Problem(
                    f"web:cert:{c['name']}",
                    Severity.WARNING,
                    "",  # Kastellan's own, as its backup's
                    "the proxy's certificate",
                    f"The certificate for {c['name']} isn't valid"
                    if invalid
                    else f"The certificate for {c['name']} ends in {cert['days_left']} days",
                    cert.get("error") or "Check the proxy's certificate and DNS-01 renewal",
                )
            )
    missing = [n["name"] for n in state.get("names") or [] if not n["ok"]]
    if missing:
        out.append(
            Problem(
                "web:dns",
                Severity.WARNING,
                "",
                "the hub's names",
                f"{missing[0]} doesn't resolve",
                "device pages and the hub's name need the zone's records (Settings → Names & HTTPS)",
            )
        )
    return out


async def run(hub: Hub) -> None:
    await asyncio.sleep(120)
    while True:
        if not hub.demo and hub.config.web is not None:
            with contextlib.suppress(Exception):  # next time
                hub.web_state, hub.web_state_t = await look(hub), time.time()
        await asyncio.sleep(EVERY)


def router(hub: Hub) -> APIRouter:
    api = APIRouter(prefix="/api/web")

    @api.get("/setup")
    async def setup(request: Request, again: bool = False) -> dict[str, Any]:
        """Names, certificates and the routers' DNS as they stand (admins); again: look now."""
        require_admin(request)
        if hub.demo:
            return {"domain": None, "checked": None}
        configuration = hub.config.web.model_dump(mode="json") if hub.config.web else None
        if (
            again
            or not hub.web_state
            or time.time() - hub.web_state_t > EVERY
            or hub.web_state.get("configuration") != configuration
        ):
            hub.web_state, hub.web_state_t = await look(hub), time.time()
        return hub.web_state

    return api
