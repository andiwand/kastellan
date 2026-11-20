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
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("hash-password", help="make a password hash for a user in config.yaml")
    args = parser.parse_args()
    if args.command == "hash-password":
        hash_password_command()
        return

    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Every HTTP check would otherwise log a line; the monitor logs what matters (ups and downs).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level)


def hash_password_command() -> None:
    """Ask for a password twice and print its hash, for `users: <name>: password:`."""
    import getpass

    from .auth import hash_password

    password = getpass.getpass("Password: ")
    if len(password) < 10:
        raise SystemExit("Use at least 10 characters.")
    if getpass.getpass("Again: ") != password:
        raise SystemExit("The two don't match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
