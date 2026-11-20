from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DeviceInfo:
    model: str | None = None
    name: str | None = None  # what the device calls itself
    firmware: str | None = None  # e.g. "FRITZ!OS 8.25"
    details: dict[str, str] = field(default_factory=dict)


class Driver:
    name = ""

    def __init__(self, host: str) -> None:
        self.host = host

    async def info(self) -> DeviceInfo | None:
        """What the device says about itself, or None when it can't be asked right now."""
        return None
