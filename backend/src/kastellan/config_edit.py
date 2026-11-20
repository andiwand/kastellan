"""Changing config.yaml from the app: the whole file (the editor), or one device (the forms).

The file stays the one source of truth, and stays the person's file:

- device edits change the YAML in place (ruamel round-trip): comments, order and the way checks
  are written stay; only what the form covers changes (name, address, kind, tags, always on,
  exposed, checks); everything else on the device (ssh, docker, driver, ...) is kept as it is;
- a change is checked before it is written, with the same rules as loading (hub.check), so a
  save never breaks the running hub;
- `version` (a hash of the file) makes saving fail instead of overwriting a change made in the
  meantime, by hand or by someone else;
- the file is replaced in one step (a temporary file, renamed), and the version before is kept
  in <data>/config-backups/ (the last KEEP);
- who changed what goes to the audit log (api side).
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import io
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .config import ID, Check

KEEP = 30  # backups kept
DEFAULT_TIMEOUT = Check.model_fields["timeout"].default


class Conflict(Exception):
    """The file changed since it was read."""


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _yaml() -> YAML:
    y = YAML()  # round-trip: comments and order stay
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 4096
    return y


def read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def write(path: Path, backups: Path, old: str, new: str) -> None:
    """Replace the file in one step, keeping the old one as a backup (private: it holds the
    users' password hashes and tokens)."""
    path = path.resolve()  # a symlinked config.yaml: change the file it points to, keep the link
    if old:
        backups.mkdir(parents=True, exist_ok=True)
        os.chmod(backups, 0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        fd = os.open(backups / f"config-{stamp}.yaml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(old)
        for stale in sorted(backups.glob("config-*.yaml"))[:-KEEP]:
            stale.unlink(missing_ok=True)
    st = path.stat() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(new)
        os.chmod(tmp, st.st_mode & 0o777 if st else 0o600)
        if st is not None:
            # The same owner as before: a hub running as root (Docker) mustn't take the file away
            # from the person who edits it by hand. Best effort: only root may give files away.
            with contextlib.suppress(OSError):
                os.chown(tmp, st.st_uid, st.st_gid)
        try:
            os.replace(tmp, path)
        except OSError:
            # A file mounted on its own (Docker: -v ./config.yaml:/config/config.yaml) can't be
            # replaced, only written over.
            path.write_text(new)
    finally:
        Path(tmp).unlink(missing_ok=True)


# Values that are secrets: masked in the audit log (which is kept for good).
SECRET = re.compile(
    r"^([+-]?\s*-?\s*(?:password|token|secret|\w*api_key|\w*_key|webhook|ntfy|smtp|url)\s*:\s*)\S.*$",
    re.IGNORECASE,
)


def diff(old: str, new: str) -> str:
    """What changed, for the audit log; secrets masked."""
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", lineterm="", n=0)
    return "\n".join(SECRET.sub(r"\1***", line) for line in lines)[:4000]


# --- One device, from the forms ---------------------------------------------------------------
#
# ruamel keeps a comment written *above* a key as the trailing comment of whatever node came
# just before it: the header comment of the next device ("# --- the NAS ---"), or a blank line
# and comment before `users:`, hang off the last value of the device above. So the device is
# changed in place, and its tail (that trailing comment) is taken off before and put back on
# whatever is last afterwards; a deleted device hands its tail to the one before it.

FORM_FIELDS = ("name", "host", "kind", "tags", "always_on", "exposed", "checks")
DEFAULTS: dict[str, Any] = {"kind": "other", "exposed": False, "always_on": None, "tags": [], "name": ""}


def _check_node(check: dict[str, Any]) -> Any:
    """A check as it is written by hand: `ping`, or `{http: url, name: ...}`; defaults left out."""
    kind = check["type"]
    target = check.get("target")
    if kind == "tcp" and isinstance(target, str) and target.strip().isdigit():
        target = int(target)
    if kind == "ping":
        target = None
    default_name = "ping" if kind == "ping" else f"{kind}:{target}"
    extras: dict[str, Any] = {}
    for key in ("name", "expect", "verify", "tls", "interval", "timeout"):
        value = check.get(key)
        if value in (None, "") or (key == "verify" and value is False):
            continue
        if (key == "name" and value == default_name) or (key == "timeout" and value == DEFAULT_TIMEOUT):
            continue  # the default: not written
        extras[key] = value
    if kind == "ping" and not extras:
        return "ping"
    node = CommentedMap()
    node[kind] = target
    node.update(extras)
    return node


def _normal(node: Any) -> dict[str, Any] | None:
    try:
        return Check.model_validate(_plain(node)).model_dump()
    except ValueError:
        return None


def _plain(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    return node


def _deepest_last(node: Any) -> tuple[Any, Any, int] | None:
    """Where a trailing comment after `node` lives: (container, key or index, slot)."""
    found = None
    # Not into a flow collection (`[a, b]`): a comment after it belongs next to it, not inside.
    while isinstance(node, CommentedMap | CommentedSeq) and len(node) and not node.fa.flow_style():
        key = list(node)[-1] if isinstance(node, CommentedMap) else len(node) - 1
        found = (node, key, 2 if isinstance(node, CommentedMap) else 0)
        node = node[key]
    return found


def _take_tail(node: Any) -> tuple[str, str]:
    """Take the comment after the last value under `node`: (its own end-of-line comment, what
    follows on the next lines: blank lines, the next thing's header)."""
    where = _deepest_last(node)
    if where is None:
        return "", ""
    container, key, slot = where
    slots = container.ca.items.get(key)
    token = slots[slot] if slots and len(slots) > slot else None
    if token is None:
        return "", ""
    slots[slot] = None
    eol, _, rest = token.value.partition("\n")
    return eol, rest


def _put_tail(node: Any, eol: str, rest: str) -> None:
    where = _deepest_last(node)
    if where is None or not (eol.strip() or rest):
        return
    container, key, slot = where
    slots = container.ca.items.setdefault(key, [None, None, None, None] if slot == 2 else [None, None])
    old = slots[slot]
    own = old.value.partition("\n")[0] if old is not None else ""
    text = (own or eol) + "\n" + rest
    if old is not None:
        old.value = text
    else:
        from ruamel.yaml.error import CommentMark
        from ruamel.yaml.tokens import CommentToken

        slots[slot] = CommentToken(text, CommentMark(0), None)


def _set_checks(device: CommentedMap, checks: list[dict[str, Any]]) -> None:
    """Change the device's checks to the form's, keeping each unchanged one as written (with its
    comments), and the list itself when nothing changed."""
    present = "checks" in device
    old = device.get("checks")
    old_items = list(old) if isinstance(old, CommentedSeq) else []
    wanted = [_normal(_check_node(c)) for c in checks]
    current = [_normal(n) for n in old_items] if present else [_normal("ping")]
    if wanted == current:
        return  # as it is (also `checks: []`, and no `checks:` meaning a ping)
    if not checks:
        device["checks"] = CommentedSeq()  # nothing is checked; the device is only shown
        return
    seq = CommentedSeq()
    used: set[int] = set()
    for check, norm in zip(checks, wanted, strict=True):
        index = next((i for i, n in enumerate(old_items) if i not in used and _normal(n) == norm), None)
        if index is None:
            seq.append(_check_node(check))
            continue
        used.add(index)
        seq.append(old_items[index])
        if isinstance(old, CommentedSeq) and index in old.ca.items:
            seq.ca.items[len(seq) - 1] = old.ca.items[index]  # the comment on a plain `- ping`
    if isinstance(old, CommentedSeq):
        seq.ca.comment = old.ca.comment
    device["checks"] = seq


def _place(device: CommentedMap, key: str, value: Any) -> None:
    """Set a key; a new one goes where it belongs among the form's keys, not at the end."""
    if key in device:
        device[key] = value
        return
    rank = FORM_FIELDS.index(key)
    keys = list(device)
    pos = next((i for i, k in enumerate(keys) if k in FORM_FIELDS and FORM_FIELDS.index(k) > rank), None)
    if pos is None:
        pos = next((i for i, k in enumerate(keys) if k not in FORM_FIELDS), len(keys))
    device.insert(pos, key, value)


def edit_device(text: str, id: str, fields: dict[str, Any] | None, new: bool = False) -> str:
    """config.yaml with device `id` set to `fields` (the form's), added (new), or removed (None)."""
    y = _yaml()
    doc = y.load(text) if text.strip() else None
    if doc is None:
        doc = CommentedMap()
    if not isinstance(doc, CommentedMap):
        raise ValueError("config.yaml isn't a mapping")
    devices = doc.get("devices")
    if devices is None:
        _place_top(doc, "devices", CommentedMap())
        devices = doc["devices"]
    if not isinstance(devices, CommentedMap):
        raise ValueError("devices: isn't a mapping")
    if not ID.match(id):
        raise ValueError("device ids are lowercase letters, digits, - and _")
    if fields is None:
        if id not in devices:
            raise KeyError(id)
        ids = list(devices)
        before = ids[ids.index(id) - 1] if ids.index(id) > 0 else None
        _, rest = _take_tail(devices[id])  # the next device's header, or what precedes users:
        if before is not None:
            eol, _ = _take_tail(devices[before])  # drop the deleted device's own header
            del devices[id]
            _put_tail(devices[before], eol, rest)
        else:
            del devices[id]
            if len(devices) and rest.strip():
                header = [ln.strip().lstrip("#").strip() for ln in rest.splitlines() if ln.strip()]
                devices.yaml_set_comment_before_after_key(
                    next(iter(devices)), before="\n".join(header), indent=2
                )
        for other in devices.values():  # nothing may hang off a device that is gone
            if isinstance(other, CommentedMap) and other.get("parent") == id:
                eol, rest = _take_tail(other)
                del other["parent"]
                _put_tail(other, eol, rest)
    else:
        if new and id in devices:
            raise ValueError(f"there is a device {id!r} already")
        if not new and id not in devices:
            raise KeyError(id)
        if new:
            last = list(devices)[-1] if len(devices) else None
            eol, rest = _take_tail(devices[last]) if last is not None else ("", "")
            devices[id] = CommentedMap()
            if last is not None:
                _put_tail(devices[last], eol, "")
            tail = ("", rest)
        device = devices[id]
        if not isinstance(device, CommentedMap):
            device = devices[id] = CommentedMap()
        if not new:
            tail = _take_tail(device)
        for key in FORM_FIELDS:
            value = fields.get(key)
            if key == "checks":
                _set_checks(device, value or [])
            elif value in (None, "", []) or (value == DEFAULTS.get(key) and key not in device):
                device.pop(key, None)  # unset, or a default not written
            elif key == "exposed" and value is False:
                device.pop(key, None)
            elif key == "tags":
                tags = CommentedSeq(value)
                tags.fa.set_flow_style()
                _place(device, key, tags)
            else:
                _place(device, key, value)
        _put_tail(device, *tail)
    out = io.StringIO()
    y.dump(doc, out)
    return _keep_gaps(text, out.getvalue())


def _place_top(doc: CommentedMap, key: str, value: Any) -> None:
    doc[key] = value


def _keep_gaps(old: str, new: str) -> str:
    """A blank line before a top-level key (`users:`) stays: ruamel files it under the last
    device, so a device added at the end would take it along."""
    gap_before = set()
    lines = old.splitlines()
    for i, line in enumerate(lines):
        if i and re.match(r"^[A-Za-z_]\w*:", line) and not lines[i - 1].strip():
            gap_before.add(line.split(":")[0])
    out: list[str] = []
    for line in new.splitlines():
        key = re.match(r"^([A-Za-z_]\w*):", line)
        if key and key.group(1) in gap_before and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out) + "\n"
