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
import errno
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
        except OSError as e:
            # A file mounted on its own (Docker: -v ./config.yaml:/config/config.yaml) can't be
            # replaced, only written over.
            if e.errno != errno.EBUSY:
                raise
            path.write_text(new)
    finally:
        Path(tmp).unlink(missing_ok=True)


# Keys whose values are secrets, and credentials inside URLs: masked in the audit log, which is
# kept for good. Masked on the parsed YAML (any style: block, flow, multi-line), then diffed.
SECRET_KEY = re.compile(r"^(password|token|secret|smtp|ntfy|webhook|key|\w*_key)$", re.IGNORECASE)
URL_LOGIN = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]*@", re.IGNORECASE)


def _mask(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: "***" if SECRET_KEY.match(str(k)) and v is not None else _mask(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_mask(v) for v in node]
    if isinstance(node, str):
        return URL_LOGIN.sub(r"\1***@", node)
    return node


def _masked_text(text: str) -> str | None:
    try:
        data = YAML(typ="safe").load(text) if text.strip() else {}
    except Exception:
        return None
    out = io.StringIO()
    y = YAML()
    y.default_flow_style = False
    y.width = 4096
    y.dump(_mask(data), out)
    return out.getvalue()


def diff(old: str, new: str) -> str:
    """What changed, for the audit log; secrets masked (on the parsed config, whatever style it
    is written in). If either version can't be read, only that it changed."""
    a, b = _masked_text(old), _masked_text(new)
    if a is None or b is None:
        return "(changed; a version couldn't be read, so the change isn't shown)"
    if a == b:
        return "(only secrets, comments or layout changed)" if old != new else ""
    lines = difflib.unified_diff(a.splitlines(), b.splitlines(), "before", "after", lineterm="", n=0)
    return "\n".join(lines)[:4000]


# --- One device, from the forms ---------------------------------------------------------------
#
# ruamel keeps a comment written *above* a key as the trailing comment of whatever node came
# just before it: the header comment of the next device ("# --- the NAS ---"), or a blank line
# and comment before `users:`, hang off the last value of the device above. So the device is
# changed in place, and its tail (that trailing comment) is taken off before and put back on
# whatever is last afterwards; a deleted device hands its tail to the one before it.

FORM_FIELDS = (
    "name",
    "host",
    "kind",
    "site",
    "runs_on",
    "ups",
    "powered_by",
    "tags",
    "always_on",
    "exposed",
    "checks",
)
DEFAULTS: dict[str, Any] = {
    "kind": "other",
    "site": None,
    "exposed": False,
    "always_on": None,
    "tags": [],
    "name": "",
}


def _check_node(check: dict[str, Any]) -> Any:
    """A check as it is written by hand: `ping`, or `{http: url, name: ...}`; defaults left out."""
    kind = check["type"]
    target = check.get("target")
    if kind == "tcp" and isinstance(target, str) and target.strip().isdigit():
        target = int(target)
    if kind in ("ping", "dhcp"):
        target = None
    default_name = kind if kind in ("ping", "dhcp") else f"{kind}:{target}"
    extras: dict[str, Any] = {}
    for key in ("name", "expect", "server", "verify", "tls", "interval", "timeout", "exposed"):
        value = check.get(key)
        if value in (None, "", []) or (key in ("verify", "exposed") and value is False):
            continue
        if key == "expect" and isinstance(value, list):
            value = value[0] if len(value) == 1 else CommentedSeq(value)  # dns: one address, or a list
        if (key == "name" and value == default_name) or (key == "timeout" and value == DEFAULT_TIMEOUT):
            continue  # the default: not written
        extras[key] = value
    if kind in ("ping", "dhcp") and not extras:
        return kind
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


def _take_tail(node: Any) -> str:
    """Take what follows the last value under `node` on the next lines (blank lines, the next
    thing's header comment). Its own end-of-line comment stays where it is: on its line."""
    where = _deepest_last(node)
    if where is None:
        return ""
    container, key, slot = where
    slots = container.ca.items.get(key)
    token = slots[slot] if slots and len(slots) > slot else None
    if token is None:
        return ""
    eol, _, rest = token.value.partition("\n")
    if eol.strip():
        token.value = eol + "\n"
    else:
        slots[slot] = None
    return rest


def _put_tail(node: Any, rest: str) -> None:
    """Put `rest` after the last value under `node` (after its own end-of-line comment)."""
    where = _deepest_last(node)
    if where is None or not rest:
        return
    container, key, slot = where
    slots = container.ca.items.setdefault(key, [None, None, None, None] if slot == 2 else [None, None])
    old = slots[slot]
    own = old.value.partition("\n")[0] if old is not None else ""
    text = own + "\n" + rest
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
        devices = doc["devices"] = CommentedMap()
    if not isinstance(devices, CommentedMap):
        raise ValueError("devices: isn't a mapping")
    if not ID.match(id):
        raise ValueError("device ids are lowercase letters, digits, - and _")
    if fields is None:
        if id not in devices:
            raise KeyError(id)
        # A UPS read through this device would have nowhere to be read from (and no address).
        for uid, other in devices.items():
            link = other.get("ups") if isinstance(other, dict) else None
            if isinstance(link, dict) and link.get("via") == id:
                name = other.get("name") or uid
                raise ValueError(f"{id} reads the UPS {name}: change its 'read by' first")
        ids = list(devices)
        before = ids[ids.index(id) - 1] if ids.index(id) > 0 else None
        rest = _take_tail(devices[id])  # the next device's header, or what precedes users:
        if before is not None:
            _take_tail(devices[before])  # the deleted device's own header goes with it
            del devices[id]
            _put_tail(devices[before], rest)
        else:
            del devices[id]
            if len(devices) and rest.strip():
                header = [ln.strip().lstrip("#").strip() for ln in rest.splitlines() if ln.strip()]
                devices.yaml_set_comment_before_after_key(
                    next(iter(devices)), before="\n".join(header), indent=2
                )
        for other in devices.values():  # nothing may hang off a device that is gone
            if not isinstance(other, CommentedMap):
                continue
            rest = _take_tail(other)
            for key in ("parent", "powered_by"):
                if other.get(key) == id:
                    del other[key]
            if isinstance(other.get("runs_on"), dict) and other["runs_on"].get("host") == id:
                del other["runs_on"]
            _put_tail(other, rest)
        groups = doc.get("groups")  # nor stay in a group; a group left empty goes too
        if isinstance(groups, CommentedMap):
            for gid in list(groups):
                members = groups[gid].get("devices") if isinstance(groups[gid], dict) else None
                if isinstance(members, list) and id in members:
                    members.remove(id)
                    if not members:
                        del groups[gid]
            if not len(groups):
                del doc["groups"]
    else:
        if new and id in devices:
            raise ValueError(f"there is a device {id!r} already")
        if not new and id not in devices:
            raise KeyError(id)
        if new:
            last = list(devices)[-1] if len(devices) else None
            tail = _take_tail(devices[last]) if last is not None else ""
            devices[id] = CommentedMap()
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
            elif key in device and _plain(device[key]) == value:
                continue  # as written already (its comments and style stay)
            elif isinstance(value, dict):  # runs_on, ups
                _place(device, key, CommentedMap({k: v for k, v in value.items() if v not in (None, "")}))
            elif key == "tags":
                tags = CommentedSeq(value)
                tags.fa.set_flow_style()
                _place(device, key, tags)
            else:
                _place(device, key, value)
        _put_tail(device, tail)
    out = io.StringIO()
    y.dump(doc, out)
    return _keep_gaps(text, out.getvalue())


def set_device_key(text: str, id: str, key: str, value: Any) -> str:
    """config.yaml with one key of a device set (e.g. collect: after installing the collector),
    or removed (None), everything else as it was."""
    y = _yaml()
    doc = y.load(text)
    device = (doc or {}).get("devices", {}).get(id) if isinstance(doc, CommentedMap) else None
    if not isinstance(device, CommentedMap):
        raise KeyError(id)
    tail = _take_tail(device)
    if value is None:
        device.pop(key, None)
        _put_tail(device, tail)
        out = io.StringIO()
        y.dump(doc, out)
        return _keep_gaps(text, out.getvalue())
    node: Any = value
    if isinstance(value, dict):
        node = CommentedMap(value)
        if not value:
            node.fa.set_flow_style()
    if key in device:
        device[key] = node
    else:
        keys = list(device)
        pos = keys.index("checks") if "checks" in keys else len(keys)
        device.insert(pos, key, node)
    _put_tail(device, tail)
    out = io.StringIO()
    y.dump(doc, out)
    return _keep_gaps(text, out.getvalue())


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


def _commented(value: Any) -> Any:
    """Plain data as ruamel's nodes, which can carry comments."""
    if isinstance(value, dict) and not isinstance(value, CommentedMap):
        out = CommentedMap()
        for k, v in value.items():
            out[k] = _commented(v)
        return out
    if isinstance(value, list) and not isinstance(value, CommentedSeq):
        return CommentedSeq(_commented(v) for v in value)
    return value


def _merge(node: Any, value: Any) -> Any:
    """`value` written over `node`, keeping the node (and its comments) where the structure
    stays: keys that remain keep their place and comments, gone keys go, a list that is the same
    stays as it was written (flow or block); what followed the node (the next section's header)
    stays after it."""
    if isinstance(node, CommentedMap) and isinstance(value, dict):
        tail = _take_tail(node)
        for key in [k for k in node if k not in value]:
            del node[key]
        for key, v in value.items():
            node[key] = _merge(node[key], v) if key in node else _commented(v)
        _put_tail(node, tail)
        return node
    if isinstance(node, CommentedSeq | CommentedMap) and _plain(node) == value:
        return node
    return _commented(value)


def set_top(text: str, key: str, value: Any) -> str:
    """config.yaml with one top-level setting (`alarms:`, `notify:`, `groups:` ...) set to
    `value`, or removed when it is None (or empty); the rest of the file as it was, comments
    included (what followed the setting stays where it was)."""
    y = _yaml()
    doc = y.load(text) if text.strip() else None
    if doc is None:
        doc = CommentedMap()
    if not isinstance(doc, CommentedMap):
        raise ValueError("config.yaml isn't a mapping")
    keys = list(doc)
    empty = value is None or value == {} or value == []
    if key in doc:
        nested = isinstance(doc[key], CommentedMap | CommentedSeq)
        tail = _take_tail(doc[key]) if nested else ""
        if empty:
            before = keys[keys.index(key) - 1] if keys.index(key) > 0 else None
            if not nested:  # a scalar's following lines sit on the key itself
                slots = doc.ca.items.get(key)
                token = slots[2] if slots and len(slots) > 2 else None
                tail = token.value.partition("\n")[2] if token is not None else ""
            del doc[key]
            if before is not None and tail:
                if isinstance(doc[before], CommentedMap | CommentedSeq):
                    _put_tail(doc[before], tail)
                else:
                    _put_scalar_tail(doc, before, tail)
        else:
            doc[key] = _merge(doc[key], value)
            if tail and isinstance(doc[key], CommentedMap | CommentedSeq):
                _put_tail(doc[key], tail)
    elif not empty:
        doc[key] = _commented(value)
    out = io.StringIO()
    y.dump(doc, out)
    return _keep_gaps(text, out.getvalue())


def _put_scalar_tail(doc: CommentedMap, key: str, rest: str) -> None:
    """Put `rest` on the lines after a top-level scalar (`check_interval: 60`)."""
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    slots = doc.ca.items.setdefault(key, [None, None, None, None])
    old = slots[2]
    own = old.value.partition("\n")[0] if old is not None else ""
    slots[2] = CommentToken(own + "\n" + rest, CommentMark(0), None)


def _device_lines(lines: list[str], id: str) -> tuple[int, int, int]:
    """Where device `id` is written: its key's line, the end of its block (before any blank
    lines and the next thing's header comment), and the indent of its contents."""
    doc = YAML().load("\n".join(lines))
    devices = doc.get("devices") if isinstance(doc, CommentedMap) else None
    if not isinstance(devices, CommentedMap) or id not in devices:
        raise KeyError(id)
    start, col = devices.lc.key(id)
    if lines[start].split(":", 1)[1].split("#", 1)[0].strip():
        raise ValueError("the device is written on one line; change it in config.yaml")

    def depth(line: str) -> int:
        return len(line) - len(line.lstrip())

    def comment(line: str) -> bool:
        return line.lstrip().startswith("#")

    # It ends at the next line of YAML as far left as its key; comments on the way (a line
    # commented out at column 0 included) are inside it, except those just before what's next.
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or comment(lines[end]) or depth(lines[end]) > col):
        end += 1
    while end > start + 1 and (
        not lines[end - 1].strip() or (comment(lines[end - 1]) and depth(lines[end - 1]) <= col)
    ):
        end -= 1
    body = [ln for ln in lines[start + 1 : end] if ln.strip() and not ln.lstrip().startswith("#")]
    indent = min((len(ln) - len(ln.lstrip()) for ln in body), default=col + 2)
    return start, end, indent


def device_text(text: str, id: str) -> str:
    """A device's part of config.yaml as written (comments and all), without its id line."""
    lines = text.splitlines()
    start, end, indent = _device_lines(lines, id)
    cut = " " * indent  # a comment less indented than the contents is moved in to them
    return "".join(
        (ln[indent:] if ln.startswith(cut) else ln.lstrip()) + "\n" for ln in lines[start + 1 : end]
    )


def set_device_text(text: str, id: str, block: str) -> str:
    """config.yaml with a device's part replaced by `block` (as `device_text` gives it); every
    other line stays exactly as it was."""
    lines = text.splitlines()
    start, end, indent = _device_lines(lines, id)
    new = [" " * indent + ln if ln.strip() else "" for ln in block.expandtabs(2).rstrip().splitlines()]
    out = "\n".join([*lines[: start + 1], *new, *lines[end:]]) + "\n"
    # Only this device may change: an anchor or alias in its text must not reach others.
    before, after = YAML(typ="safe").load(text) or {}, YAML(typ="safe").load(out) or {}
    for doc in (before, after):
        if isinstance(doc, dict) and isinstance(doc.get("devices"), dict):
            doc["devices"].pop(id, None)
    if before != after:
        raise ValueError("this would change more than this device (an anchor or alias?); use config.yaml")
    return out
