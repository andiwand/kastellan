"""Backups that report in: a backup job (restic, borg, a NAS's own) calls the hub when it is
done, and the hub raises an alarm when none came in time, or the last one said it failed.

    curl -fsS -m 30 -X POST -H "Authorization: Bearer $TOKEN" https://<hub>/api/backups/<id>/ok

`/fail` (with the reason as the body) says it failed. Each backup has its own token; the hub
keeps only its SHA-256 (<data>/backups.json), so it is shown once, when made."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .alarms import Problem, Severity
from .config import Config

log = logging.getLogger(__name__)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Backups:
    def __init__(self, folder: Path) -> None:
        self.path = folder / "backups.json"
        # id → token (hash), since (first known), ok / failed (times), message
        self.state: dict[str, dict[str, Any]] = {}
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict):
                raise ValueError("expected an object")
            for id, entry in data.items():
                if not isinstance(entry, dict) or not isinstance(entry.get("since"), str):
                    continue
                try:
                    for key in ("since", "ok", "failed"):
                        if key in entry and datetime.fromisoformat(entry[key]).tzinfo is None:
                            raise ValueError("timestamp has no timezone")
                    if any(not isinstance(v, str) for v in entry.values()):
                        continue
                except (TypeError, ValueError):
                    continue
                self.state[id] = entry
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            log.warning("cannot read %s (%s); starting afresh", self.path, e)

    def _save(self, state: dict[str, dict[str, Any]]) -> None:
        """Publish in memory only after the atomic write succeeds; reports must be durable."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".backups-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        self.state = state

    def sync(self, config: Config) -> None:
        """Removed jobs, or jobs moved to another device, lose their tokens and history."""
        state = {
            id: {**e, "device": config.backups[id].device}
            for id, e in self.state.items()
            if id in config.backups
            and e.get("device", config.backups[id].device) == config.backups[id].device
        }
        for id, job in config.backups.items():
            state.setdefault(id, {"since": datetime.now(UTC).isoformat(), "device": job.device})
        if state != self.state:
            self._save(state)

    def _entry(self, id: str, now: datetime) -> dict[str, Any]:
        if id not in self.state:
            self._save({**self.state, id: {"since": now.isoformat()}})
        return self.state[id]

    def new_token(self, id: str, now: datetime) -> str:
        """A token for this backup's job; the one before stops working."""
        token = secrets.token_urlsafe(24)
        entry = {**self._entry(id, now), "token": _hash(token)}
        self._save({**self.state, id: entry})
        return token

    def valid(self, id: str, token: str) -> bool:
        stored = (self.state.get(id) or {}).get("token")
        return bool(stored) and hmac.compare_digest(stored, _hash(token))

    def report(self, id: str, ok: bool, message: str, now: datetime) -> None:
        status = "ok" if ok else "failed"
        entry = {
            **self._entry(id, now),
            status: now.isoformat(),
            "status": status,
            "message": "" if ok else " ".join(message.split())[:300],
        }
        self._save({**self.state, id: entry})

    def view(self, config: Config, now: datetime) -> list[dict[str, Any]]:
        """Each configured backup with when it last reported, for the app (never the token)."""
        out = []
        for id, b in config.backups.items():
            e = self._entry(id, now)
            out.append(
                {
                    "id": id,
                    "name": b.name,
                    "device": b.device,
                    "within_hours": b.within_hours,
                    "ok": e.get("ok"),
                    "failed": e.get("failed"),
                    "message": e.get("message") or "",
                    "since": e.get("since"),
                    "token": bool(e.get("token")),
                    "state": _state(e, b.within_hours, now),
                }
            )
        return out

    def problems(self, config: Config, now: datetime) -> list[Problem]:
        out = []
        for id, b in config.backups.items():
            e = self._entry(id, now)
            state = _state(e, b.within_hours, now)
            label = f"{config.label(b.device)}: backup {b.name}"
            if state == "failed":
                title = f"{label} failed"
                detail = e.get("message") or ""
            elif state == "late":
                last = e.get("ok")
                hours = (now - datetime.fromisoformat(last or e["since"])).total_seconds() / 3600
                title = f"{label} hasn't run for {hours:.0f} h" if last else f"{label} never reported"
                detail = f"expected within {b.within_hours:g} h"
            else:
                continue
            out.append(Problem(f"backup:{id}", Severity.WARNING, b.device, label, title, detail))
        return out


def _state(e: dict[str, Any], within_hours: float, now: datetime) -> str:
    """ok, failed (its last word was a failure), late (no success in time) or waiting (new, not
    due yet)."""
    ok, failed = e.get("ok"), e.get("failed")
    if e.get("status") == "failed" or (
        "status" not in e
        and failed
        and (not ok or datetime.fromisoformat(failed) >= datetime.fromisoformat(ok))
    ):
        return "failed"
    last = datetime.fromisoformat(ok or e["since"])
    if now - last > timedelta(hours=within_hours):
        return "late"
    return "ok" if ok else "waiting"
