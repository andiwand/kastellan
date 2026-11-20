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
    secret = commands.add_parser(
        "secret", help="store a secret (a password, a token) for config.yaml to name"
    )
    secret.add_argument("action", choices=["set", "list"])
    secret.add_argument("name", nargs="?")
    args = parser.parse_args()
    if args.command == "hash-password":
        hash_password_command()
        return
    if args.command == "secret":
        secret_command(args.action, args.name)
        return

    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Every HTTP check would otherwise log a line; the monitor logs what matters (ups and downs).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        # Never take the client's address from X-Forwarded-For: anything reaching the hub over
        # localhost could then pick any address (to slip past the brake, or to fake the audit log).
        proxy_headers=False,
        ws_max_size=1024 * 1024,  # terminal frames are small; 16 MB default frames are not needed
    )


def secret_command(action: str, name: str | None) -> None:
    """`kastellan secret set wg-easy` asks for the value and stores it in <data>/secrets (0600)."""
    import getpass

    from .secrets import Secrets

    secrets = Secrets(Settings.from_env().data / "secrets")
    if action == "list":
        print("\n".join(secrets.names()) or "(none)")
        return
    if not name:
        raise SystemExit("Name the secret: kastellan secret set <name>")
    value = getpass.getpass(f"Value for {name}: ")
    if not value:
        raise SystemExit("Nothing stored.")
    secrets.set(name, value)
    print(f"Stored {name}.")


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
