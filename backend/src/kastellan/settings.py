"""Process settings from KASTELLAN_* environment variables. What the hub manages is in config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    config: Path
    data: Path
    host: str = "0.0.0.0"
    port: int = 8100
    log_level: str = "info"
    demo: bool = False  # a made-up network with simulated results, for trying the app
    # Names the hub may be reached by (besides IP addresses and localhost); see auth_api.Guard.
    hosts: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        config = Path(os.environ.get("KASTELLAN_CONFIG", "data/config.yaml"))
        return cls(
            config=config,
            data=Path(os.environ.get("KASTELLAN_DATA", str(config.parent))),
            host=os.environ.get("KASTELLAN_HOST", "0.0.0.0"),
            port=int(os.environ.get("KASTELLAN_PORT", "8100")),
            log_level=os.environ.get("KASTELLAN_LOG_LEVEL", "info").lower(),
            demo=os.environ.get("KASTELLAN_DEMO", "") not in ("", "0", "false"),
            hosts=tuple(
                h.strip().lower() for h in os.environ.get("KASTELLAN_HOSTS", "").split(",") if h.strip()
            ),
        )
