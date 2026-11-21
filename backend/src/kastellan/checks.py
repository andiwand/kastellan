"""Reachability checks: ping, a TCP connect, an HTTP request, a DNS lookup. Each returns a Result and
never raises."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import ipaddress
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx
from icmplib import async_ping
from icmplib.exceptions import ICMPLibError, SocketPermissionError

from . import tls
from .config import Check, CheckType
from .software import Software, from_greeting, from_http


@dataclass(frozen=True)
class Result:
    up: bool
    latency_ms: float | None = None
    detail: str = ""
    software: Software | None = None  # what the service says it is, when it says
    cert: tls.Cert | None = None  # https: the certificate it presents
    address: str | None = None  # http to a name: the address it led to


GREETING_WAIT = 0.8  # seconds a TCP check listens for a server that speaks first (SSH, FTP, SMTP)


# Certificate looks still running (references, so they aren't collected half-way).
_probes: set[asyncio.Task[tls.Cert | None]] = set()

RETRY_AFTER = 2.0  # seconds before a failed check is tried once more

# Set when the operating system refuses unprivileged ICMP, so the app can say "the hub cannot
# ping" once instead of showing every device as down.
ping_refused: str | None = None


async def run(host: str, check: Check) -> Result:
    """Run a check; a failure counts only if a second try a moment later fails too, so a single
    lost packet on Wi-Fi or a VPN does not flip a device to down."""
    result = await _run_once(host, check)
    if result.up or check.type is CheckType.PING:  # ping already sends three packets
        return result
    await asyncio.sleep(RETRY_AFTER)
    return await _run_once(host, check)


async def _run_once(host: str, check: Check) -> Result:
    try:
        async with asyncio.timeout(check.timeout + 1):
            match check.type:
                case CheckType.PING:
                    return await _ping(host, check)
                case CheckType.TCP:
                    return await _tcp(host, check)
                case CheckType.HTTP:
                    return await _http(check)
                case CheckType.DNS:
                    return await _dns(check)
    except TimeoutError:
        return Result(False, detail="timed out")
    except Exception as e:  # a check reports a failure, it never takes the monitor down
        return Result(False, detail=f"{type(e).__name__}: {e}")


async def _ping(host: str, check: Check) -> Result:
    global ping_refused
    try:
        # Unprivileged ICMP: works on macOS, and on Linux where net.ipv4.ping_group_range allows
        # it (Docker sets that for a container's own network, not with host networking).
        # Three packets; any reply means up. They go one after another, each waiting its share
        # of the check's time, so all three are sent before the overall limit.
        wait = max(0.1, (check.timeout - 2 * 0.3) / 3)
        reply = await async_ping(host, count=3, interval=0.3, timeout=wait, privileged=False)
    except SocketPermissionError as e:
        ping_refused = str(e) or "the operating system does not allow this program to ping"
        return Result(False, detail="the hub may not ping (see README)")
    except ICMPLibError as e:
        return Result(False, detail=str(e) or type(e).__name__)
    ping_refused = None
    if not reply.is_alive:
        return Result(False, detail="no reply")
    return Result(True, round(reply.avg_rtt, 2))


async def _tcp(host: str, check: Check) -> Result:
    start = time.perf_counter()
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, check.target), check.timeout)
    except TimeoutError:
        return Result(False, detail="timed out")
    except OSError as e:
        return Result(False, detail=e.strerror or str(e))
    latency = (time.perf_counter() - start) * 1000
    # Servers of some protocols greet first and name themselves; others stay silent, which is fine.
    greeting = b""
    with contextlib.suppress(TimeoutError, OSError):
        greeting = await asyncio.wait_for(reader.read(256), GREETING_WAIT)
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    software = from_greeting(greeting.decode("ascii", errors="replace")) if greeting else None
    return Result(True, round(latency, 2), software=software)


async def _dns(check: Check) -> Result:
    """Does the name resolve (A and AAAA), and, with `expect`, to every one of those addresses.
    Asks `server`, or the system's resolvers (resolv.conf)."""
    assert isinstance(check.target, str)
    resolver = dns.asyncresolver.Resolver(configure=check.server is None)
    if check.server:
        resolver.nameservers = [check.server]
    resolver.lifetime = check.timeout

    async def ask(rdtype: str) -> list[str]:
        try:
            # search: a single name (`nas`) gets resolv.conf's search domains, as other tools do
            return [r.to_text() for r in await resolver.resolve(check.target, rdtype, search=True)]
        except dns.resolver.NoAnswer:
            return []

    async def timed(rdtype: str) -> tuple[list[str], float]:
        found = await ask(rdtype)
        return found, time.perf_counter()

    start = time.perf_counter()
    a, aaaa = asyncio.ensure_future(timed("A")), asyncio.ensure_future(timed("AAAA"))
    try:
        # One answer is enough: some home routers drop AAAA questions. Once A has answered,
        # AAAA gets a moment more, not the whole time limit; the latency is the first answer's.
        await asyncio.wait([a])
        if a.exception() is None and a.result()[0]:
            await asyncio.wait([aaaa], timeout=0.3)
        else:
            await asyncio.wait([aaaa])
        results = [t.result() if t.done() and t.exception() is None else t for t in (a, aaaa)]
        answers = [r for r in results if isinstance(r, tuple)]
        found = [x for r, _ in answers for x in r]
        if not found:
            failed = next((t.exception() for t in (a, aaaa) if t.done() and t.exception()), None)
            if failed is not None:
                raise failed
    except dns.resolver.NXDOMAIN:
        return Result(False, detail="no such name")
    except dns.exception.Timeout:
        return Result(False, detail="the resolver didn't answer")
    except dns.exception.DNSException as e:
        return Result(False, detail=str(e) or type(e).__name__)
    finally:
        aaaa.cancel()
    first = min((t for r, t in answers if r), default=time.perf_counter())
    latency = round((first - start) * 1000, 2)
    shown = ", ".join(found[:3]) + (", …" if len(found) > 3 else "")
    if not found:
        return Result(False, latency, "no addresses")
    expected = {str(ipaddress.ip_address(x)) for x in check.expect or []}  # as dnspython writes them
    missing = expected - {str(ipaddress.ip_address(a)) for a in found}
    if missing:
        return Result(False, latency, f"{shown}; not {', '.join(sorted(missing))}")
    return Result(True, latency, shown)


async def _http(check: Check) -> Result:
    """The page, and for https its certificate (its own handshake, run alongside). For a name,
    also the address it led to: which way a domain goes (inside, or out and back in)."""
    assert isinstance(check.target, str)
    url = urlsplit(check.target)
    host = url.hostname or ""
    port = url.port or (443 if url.scheme == "https" else 80)
    cert = None
    if url.scheme == "https":
        # Its own handshake, alongside the request and within the same time; see _cert_of.
        cert = asyncio.create_task(tls.certificate(host, port, host, check.timeout))
        _probes.add(cert)
        cert.add_done_callback(_probes.discard)
    start = time.perf_counter()
    address: str | None = None
    async with httpx.AsyncClient(verify=check.verify, timeout=check.timeout) as client:
        try:
            # Streamed: a camera's endless MJPEG or a large file must not be read whole.
            async with client.stream("GET", check.target) as response:
                latency = round((time.perf_counter() - start) * 1000, 2)
                stream = response.extensions.get("network_stream")
                with contextlib.suppress(Exception):
                    address = str(stream.get_extra_info("server_addr")[0])
                status = response.status_code
                up = status == check.expect if check.expect is not None else status < 500
                result = Result(up, latency, f"HTTP {status}", await _software(response) if up else None)
        except httpx.HTTPError as e:
            result = Result(False, detail=f"{type(e).__name__}: {e}".rstrip(": "))
    if _is_address(host):
        address = None  # the target is the address already
    elif address is None:
        address = await _resolve(host, port)
    return dataclasses.replace(result, cert=await _cert_of(cert) if cert else None, address=address)


async def _cert_of(task: asyncio.Task[tls.Cert | None]) -> tls.Cert | None:
    """The certificate if the look at it is done in a moment more; it never decides up or down."""
    try:
        return await asyncio.wait_for(asyncio.shield(task), 0.4)
    except TimeoutError:
        return None  # keeps going in the background, and is cached for the next run


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


async def _resolve(host: str, port: int) -> str | None:
    """Where a name points now, for a check that got no answer (or None). Briefly: together
    with _cert_of it stays inside the check's second of slack (run_once)."""
    try:
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port), 0.4)
    except (OSError, TimeoutError):
        return None
    return str(infos[0][4][0]) if infos else None


async def _software(response: httpx.Response) -> Software | None:
    """What the page says it is. Only from answers that work (an error page names the proxy in
    front, not the service), reading at most 20 kB, and never failing the check over it."""
    if not (200 <= response.status_code < 300 or response.status_code == 401):
        return None
    try:
        body = ""
        if "html" in response.headers.get("content-type", ""):
            head = bytearray()
            async for chunk in response.aiter_bytes():
                head += chunk
                if len(head) >= 20000:
                    break
            body = bytes(head[:20000]).decode(response.encoding or "utf-8", errors="replace")
        return from_http(dict(response.headers), body)
    except Exception:  # a wrong charset name, or anything else odd about the page
        return None
