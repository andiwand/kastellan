"""The open terminals, so ending a session can end its shells at once.

A terminal registers with the user and the session it was opened with. Signing out ends the
session's terminals; signing out everywhere, or a config.yaml without the user, with a new
password or another role, ends all of the user's. Each terminal also re-checks its session
every few seconds (terminal_api), in case something here was missed.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field


def token_key(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


@dataclass(eq=False)
class Handle:
    user: str
    session: str  # token_key of the session it was opened with
    device: str
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""


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
