"""The open terminals and remote desktops, so ending a session can end its shells at once.

A terminal (or desktop) registers with the user and the session it was opened with. Signing out
ends the session's terminals; signing out everywhere, or a config.yaml without the user, with a
new password or another role, ends all of the user's. Each one also re-checks its session every
few seconds (`watch`), in case something here was missed, and ends when idle or too old.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .auth_api import Who

IDLE_LIMIT = 30 * 60  # one nobody typed in (or moved the mouse in) for this long closes
MAX_SECONDS = 8 * 3600  # however busy, it ends after this long
MAX_PER_USER = 4  # terminals and desktops together
RECHECK_EVERY = 15  # seconds between checks that the session is still good


def token_key(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


@dataclass(eq=False)
class Handle:
    user: str
    session: str  # token_key of the session it was opened with
    device: str
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    opened: float = field(default_factory=time.monotonic)

    def ended(self, now: Who | None) -> str | None:
        """Why it must end now, or None; `now`: who its session belongs to at this moment."""
        if self.stop.is_set():
            return self.reason or "signed out"
        if now is None or now.user != self.user or not now.is_admin:
            return "your session ended"
        if time.monotonic() - self.opened > MAX_SECONDS:
            return f"closed after {MAX_SECONDS // 3600} hours"
        return None


async def watch(
    stop: asyncio.Event, allowed: Callable[[], str | None], last_input: Callable[[], float]
) -> str:
    """Wait until a terminal or desktop must end (its session ends, the user loses access, or it
    idles); why."""
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), RECHECK_EVERY)
        if reason := allowed():
            return reason
        if time.monotonic() - last_input() > IDLE_LIMIT:
            return f"closed after {IDLE_LIMIT // 60} minutes without input"


class Terminals:
    def __init__(self) -> None:
        self.open: set[Handle] = set()

    def count(self, user: str) -> int:
        return sum(1 for h in self.open if h.user == user)

    def add(self, user: str, token: str | None, device: str) -> Handle:
        handle = Handle(user, token_key(token), device)
        self.open.add(handle)
        return handle

    def remove(self, handle: Handle) -> None:
        self.open.discard(handle)

    def stop(self, *, user: str | None = None, token: str | None = None, reason: str = "signed out") -> None:
        key = token_key(token) if token is not None else None
        for h in self.open:
            if (user is not None and h.user == user) or (key is not None and h.session == key):
                h.reason = h.reason or reason
                h.stop.set()

    def stop_all_but(self, allowed: set[str], reason: str) -> None:
        """End the terminals of users not (or no longer) allowed to have one."""
        for h in self.open:
            if h.user not in allowed:
                h.reason = h.reason or reason
                h.stop.set()
