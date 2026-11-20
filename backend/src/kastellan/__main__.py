"""Run Kastellan: `kastellan` or `python -m kastellan`. Settings come from KASTELLAN_* variables."""

from __future__ import annotations

import argparse
import logging

import uvicorn

from . import __version__
from .api import create_app
from .settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kastellan", description="Every device on your network in one place."
    )
    parser.add_argument("--version", action="version", version=f"kastellan {__version__}")
    parser.parse_args()

    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level)


if __name__ == "__main__":
    main()
