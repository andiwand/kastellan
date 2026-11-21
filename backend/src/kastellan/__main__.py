"""Run Kastellan: `kastellan` or `python -m kastellan`. Settings come from KASTELLAN_* variables."""

from __future__ import annotations

import argparse
import logging
import sys

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
    hashing = commands.add_parser("hash-password", help="make a password hash for a user in config.yaml")
    hashing.add_argument(
        "--short", action="store_true", help="take a password under 10 characters without asking"
    )
    secret = commands.add_parser(
        "secret", help="store a secret (a password, a token) for config.yaml to name"
    )
    secret.add_argument("action", choices=["set", "list"])
    secret.add_argument("name", nargs="?")
    restoring = commands.add_parser(
        "restore", help="bring a hub back from its own backup (a backup folder or a .kbak file)"
    )
    restoring.add_argument("source", help="a copy of the backup folder, or a downloaded .kbak file")
    restoring.add_argument("--version", help="a version's id (default: the newest)")
    restoring.add_argument(
        "--force", action="store_true", help="replace existing data and configuration (hub must be stopped)"
    )
    restoring.add_argument("--list", action="store_true", help="only list the versions")
    args = parser.parse_args()
    if args.command == "restore":
        restore_command(args.source, args.version, args.force, args.list)
        return
    if args.command == "hash-password":
        hash_password_command(allow_short=args.short)
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

    data = Settings.from_env().data
    if (data / "vault.json").exists():  # this command can't unlock it: the app can
        raise SystemExit("Secrets are in the key ring now: set them in the app (Keys).")
    secrets = Secrets(data / "secrets")
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


def restore_command(source: str, version: str | None, force: bool, only_list: bool) -> None:
    """`kastellan restore /backup/kastellan`: asks for the backup's passphrase (or takes
    KASTELLAN_BACKUP_PASSPHRASE) and writes config.yaml and the data folder as the settings
    name them (KASTELLAN_CONFIG, KASTELLAN_DATA). Run it with the hub stopped."""
    import getpass
    import json
    import os
    from pathlib import Path

    from . import hubbackup

    settings = Settings.from_env()
    path = Path(source)
    if not path.exists():
        raise SystemExit(f"No {path}.")
    if path.is_file() and (only_list or version):
        raise SystemExit("--list and --version apply only to backup folders; nothing restored.")
    phrase = os.environ.get("KASTELLAN_BACKUP_PASSPHRASE") or getpass.getpass("The backup's passphrase: ")
    try:
        if path.is_dir():
            if only_list:
                header = json.loads((path / hubbackup.HEADER).read_text())
                key = hubbackup.key_from(header, phrase)
                for s in hubbackup.Store(path, lambda: key).snapshots(hubbackup.ID):
                    print(f"{s.id}  {s.taken}  {len(s.files)} files")
                return
            done = hubbackup.restore(path, phrase, settings.data, settings.config, version, force)
        else:
            done = hubbackup.restore_file(path, phrase, settings.data, settings.config, force)
    except (ValueError, OSError, KeyError) as e:
        raise SystemExit(f"Not restored: {e}") from e
    except Exception as e:  # a damaged or altered backup (AES-GCM refuses it)
        why = f"the backup is damaged or not this passphrase's ({type(e).__name__})"
        raise SystemExit(f"Not restored: {why}") from e
    print(f"Restored {done['files']} files into {settings.data} (and {settings.config}).")
    print("Start the hub and sign in as before; the key rings open with your password.")


SHORT = 10  # under this, a password is easy to guess; allowed, after a warning


def hash_password_command(allow_short: bool = False) -> None:
    """Ask for a password twice and print its hash, for `users: <name>: password:`. A short one
    is your call: a warning and a question (or --short), not a refusal. Sign-in slows down
    guessing (5 tries per address in 5 minutes), and the hub is meant to be reached only from
    home or through the VPN."""
    import getpass

    from .auth import hash_password

    password = getpass.getpass("Password: ")
    if not password:
        raise SystemExit("No password given.")
    if len(password) < SHORT and not allow_short:
        # On stderr: stdout is the hash alone, so it can be copied or piped.
        print(
            f"That is {len(password)} characters; under {SHORT} is easy to guess for anyone who "
            "reaches the hub.",
            file=sys.stderr,
        )
        print("Use it anyway? [y/N] ", end="", file=sys.stderr, flush=True)
        if sys.stdin.readline().strip().lower() not in ("y", "yes"):
            raise SystemExit("Not used.")
    if getpass.getpass("Again: ") != password:
        raise SystemExit("The two don't match.")
    print(hash_password(password))


if __name__ == "__main__":
    main()
