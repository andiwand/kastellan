"""A collector report, as the hub keeps it: rebuilt from what the host sent into known keys,
plain types, bounded sizes and finite numbers. The host is not trusted: whatever else it sends
(odd types, huge lists, deep nesting, endless names) is dropped here, before anything stores,
shows or alarms on it."""

from __future__ import annotations

import math
from typing import Any

MAX_ITEMS = 64  # filesystems, disks, sensors, services, RAID arrays
MAX_CONTAINERS = 300


def _num(value: Any, low: float = 0, high: float = 1e18) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not low <= value <= high or not math.isfinite(value):
        return None
    return float(value)


def _int(value: Any, low: int = 0, high: int = 10**18) -> int | None:
    n = _num(value, low, high)
    return int(n) if n is not None else None


def _text(value: Any, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _list(value: Any, limit: int = MAX_ITEMS) -> list[Any]:
    return value[:limit] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def normalize(raw: Any) -> dict[str, Any]:
    """The report the hub keeps; never raises."""
    r = _dict(raw)
    out: dict[str, Any] = {"version": 1, "script": _text(r.get("script"), 64)}
    errors = {
        str(k)[:40]: _text(v) or ""
        for k, v in list(_dict(r.get("errors")).items())[:20]
        if isinstance(k, str)
    }
    out["errors"] = errors

    s = _dict(r.get("system"))
    if s:
        mem, swap = _dict(s.get("memory")), _dict(s.get("swap"))
        cpu = _list(s.get("cpu"), 8)
        if len(cpu) < 4 or not all(type(n) is int and 0 <= n < 2**64 for n in cpu):
            cpu = None
        out["system"] = {
            "hostname": _text(s.get("hostname"), 100) or "",
            "kernel": _text(s.get("kernel"), 100) or "",
            "uptime": _num(s.get("uptime"), 0, 1e10) or 0.0,
            "load": [x for x in (_num(v, 0, 1e6) for v in _list(s.get("load"), 3)) if x is not None],
            "cpus": _int(s.get("cpus"), 1, 100000) or 1,
            "cpu": cpu,
            "memory": {"total": _int(mem.get("total")) or 0, "available": _int(mem.get("available")) or 0},
            "swap": {"total": _int(swap.get("total")) or 0, "free": _int(swap.get("free")) or 0},
        }

    if "filesystems" in r:
        fss = []
        for fs in _list(r.get("filesystems")):
            fs = _dict(fs)
            mount = _text(fs.get("mount"), 200)
            used, avail, total = _int(fs.get("used")), _int(fs.get("available")), _int(fs.get("total"))
            if mount is None or used is None or avail is None:
                continue
            fss.append(
                {
                    "mount": mount,
                    "device": _text(fs.get("device"), 200) or "",
                    "type": _text(fs.get("type"), 20) or "",
                    "total": total or used + avail,
                    "used": used,
                    "available": avail,
                }
            )
        out["filesystems"] = fss

    if "raid" in r:
        arrays = []
        for a in _list(r.get("raid")):
            a = _dict(a)
            name = _text(a.get("name"), 40)
            if not name:
                continue
            progress = _dict(a.get("progress"))
            arrays.append(
                {
                    "name": name,
                    "active": a.get("active") is True,
                    "level": _text(a.get("level"), 20) or "",
                    "devices": [d for d in (_text(x, 40) for x in _list(a.get("devices"))) if d],
                    "failed": [d for d in (_text(x, 40) for x in _list(a.get("failed"))) if d],
                    "status": "".join(c for c in (_text(a.get("status"), 64) or "") if c in "U_"),
                    "progress": {
                        "what": _text(progress.get("what"), 20) or "",
                        "percent": _num(progress.get("percent"), 0, 100),
                    }
                    if progress
                    else None,
                }
            )
        out["raid"] = arrays

    if "smart" in r:
        sm = _dict(r.get("smart"))
        disks = []
        for d in _list(sm.get("disks")):
            d = _dict(d)
            name = _text(d.get("disk"), 40)
            if not name:
                continue
            passed = d.get("passed")
            disks.append(
                {
                    "disk": name,
                    "model": _text(d.get("model"), 80),
                    "passed": passed if isinstance(passed, bool) else None,
                    "asleep": d.get("asleep") is True,
                    "error": _text(d.get("error"), 100),
                    "temperature": _num(d.get("temperature"), -50, 200),
                    "power_on_hours": _int(d.get("power_on_hours"), 0, 10**7),
                    **{
                        k: _int(d.get(k), 0, 10**12)
                        for k in ("reallocated", "pending", "uncorrectable", "nvme_media_errors")
                    },
                    "nvme_used_percent": _int(d.get("nvme_used_percent"), 0, 1000),
                }
            )
        out["smart"] = {"available": sm.get("available") is True, "disks": disks}

    if "activity" in r:
        out["activity"] = [
            {
                "disk": name,
                "busy_ms": _int(d.get("busy_ms"), 0, 2**63),
                "rotational": d.get("rotational") is True,
                "asleep": d.get("asleep") if isinstance(d.get("asleep"), bool) else None,
            }
            for d in (_dict(x) for x in _list(r.get("activity"), 64))
            if (name := _text(d.get("disk"), 40))
        ]

    if "temperatures" in r:
        temps = []
        for t in _list(r.get("temperatures")):
            t = _dict(t)
            value, sensor = _num(t.get("celsius"), -50, 200), _text(t.get("sensor"), 60)
            if value is None or not sensor:
                continue
            temps.append({"sensor": sensor, "celsius": value, "limit": _num(t.get("limit"), 0, 200)})
        out["temperatures"] = temps

    if "services" in r:
        failed = [x for x in (_text(v, 100) for v in _list(_dict(r.get("services")).get("failed"))) if x]
        out["services"] = {"failed": failed}

    if "updates" in r:
        u = _dict(r.get("updates"))
        out["updates"] = {
            "reboot": u.get("reboot") is True,
            "reboot_for": [x for x in (_text(v, 100) for v in _list(u.get("reboot_for"), 20)) if x],
            "available": _int(u.get("available"), 0, 10**6),
            "security": _int(u.get("security"), 0, 10**6),
            "packages": [x for x in (_text(v, 100) for v in _list(u.get("packages"), 50)) if x],
        }

    if "docker" in r:
        items = r.get("docker")
        if isinstance(items, list):
            containers = []
            for c in items[:MAX_CONTAINERS]:
                c = _dict(c)
                name, image = _text(c.get("name"), 128), _text(c.get("image"), 300)
                if not name or not image:
                    continue
                containers.append(
                    {
                        "name": name,
                        "image": image,
                        "image_id": _text(c.get("image_id"), 80) or "",
                        "digests": [x for x in (_text(v, 300) for v in _list(c.get("digests"), 10)) if x],
                        "state": _text(c.get("state"), 40) or "",
                        "status": _text(c.get("status"), 100) or "",
                        # The image kept from before its last update (short id), to go back to.
                        "previous": _text(c.get("previous"), 12),
                        "compose": _text(c.get("compose"), 300),
                        "ports": [p for p in _list(c.get("ports"), 50) if type(p) is int and 0 < p < 65536],
                    }
                )
            out["docker"] = containers
        else:
            out["docker"] = None

    if "vms" in r:
        items = r.get("vms")
        if isinstance(items, list):
            vms = []
            for v in items[:MAX_ITEMS]:
                v = _dict(v)
                name = _text(v.get("name"), 128)
                if not name:
                    continue
                vms.append(
                    {
                        "name": name,
                        "uuid": _text(v.get("uuid"), 64),
                        "state": _text(v.get("state"), 40) or "",
                        "autostart": v.get("autostart") is True,
                        "persistent": v.get("persistent") is True,
                        "vcpus": _int(v.get("vcpus"), 1, 4096),
                        "memory": _int(v.get("memory")),
                        "memory_used": _int(v.get("memory_used")),
                        "disks": [
                            {
                                "target": _text(d.get("target"), 40) or "",
                                "source": _text(d.get("source"), 300) or "",
                            }
                            for d in (_dict(x) for x in _list(v.get("disks"), 16))
                        ],
                    }
                )
            out["vms"] = vms
        else:
            out["vms"] = None
    if isinstance(r.get("ups"), list):
        out["ups"] = [_ups(_dict(u)) for u in r["ups"][:8]]
    return out


UPS_FLAGS = ("online", "on_battery", "low_battery", "replace_battery", "no_contact", "unknown")


def _ups(u: dict[str, Any]) -> dict[str, Any]:
    """One UPS as its host reads it (apcupsd or NUT): flags from a fixed set, numbers in range."""
    flags = [f for f in _list(u.get("flags"), 6) if f in UPS_FLAGS]
    return {
        "source": "nut" if u.get("source") == "nut" else "apcupsd",
        "name": _text(u.get("name"), 64),
        "model": _text(u.get("model"), 100),
        "serial": _text(u.get("serial"), 64),
        "firmware": _text(u.get("firmware"), 100),
        "status": _text(u.get("status"), 100) or "",
        "flags": list(dict.fromkeys(flags)) or ["unknown"],
        "charge": _num(u.get("charge"), 0, 100),
        "runtime": _num(u.get("runtime"), 0, 10**7),  # seconds
        "load": _num(u.get("load"), 0, 1000),
        "input_voltage": _num(u.get("input_voltage"), 0, 1000),
        "battery_voltage": _num(u.get("battery_voltage"), 0, 1000),
        "nominal_power": _num(u.get("nominal_power"), 0, 10**6),
        "last_transfer": _text(u.get("last_transfer"), 100),
        "on_battery_since": _text(u.get("on_battery_since"), 40),
        "off_battery_since": _text(u.get("off_battery_since"), 40),
        "transfers": _int(u.get("transfers"), 0, 10**6),
        "battery_date": _text(u.get("battery_date"), 40),
    }
