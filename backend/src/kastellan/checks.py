"""Reachability checks: ping, a TCP connect, an HTTP request. Each returns a Result and never raises."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

import httpx
from icmplib import async_ping
from icmplib.exceptions import ICMPLibError, SocketPermissionError

from .config import Check, CheckType


@dataclass(frozen=True)
class Result:
    up: bool
    latency_ms: float | None = None
    detail: str = ""


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
    except TimeoutError:
        return Result(False, detail="timed out")
    except Exception as e:  # a check reports a failure, it never takes the monitor down
        return Result(False, detail=f"{type(e).__name__}: {e}")


async def _ping(host: str, check: Check) -> Result:
    global ping_refused
    try:
        # Unprivileged ICMP: works on macOS, and on Linux where net.ipv4.ping_group_range allows
        # it (Docker sets that for a container's own network, not with host networking).
        # Three packets; any reply means up.
        reply = await async_ping(host, count=3, interval=0.3, timeout=check.timeout, privileged=False)
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
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, check.target), check.timeout)
    except TimeoutError:
        return Result(False, detail="timed out")
    except OSError as e:
        return Result(False, detail=e.strerror or str(e))
    latency = (time.perf_counter() - start) * 1000
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return Result(True, round(latency, 2))


async def _http(check: Check) -> Result:
    assert isinstance(check.target, str)
    start = time.perf_counter()
    async with httpx.AsyncClient(verify=check.verify, timeout=check.timeout) as client:
        try:
            response = await client.get(check.target)
        except httpx.HTTPError as e:
            return Result(False, detail=f"{type(e).__name__}: {e}".rstrip(": "))
    latency = round((time.perf_counter() - start) * 1000, 2)
    status = response.status_code
    up = status == check.expect if check.expect is not None else status < 500
    return Result(up, latency, f"HTTP {status}")
