"""CVSS 3.x base scores from vector strings (FIRST's CVSS v3.1 specification, section 7).

OSV entries from Go's and Python's databases often carry only a vector, not a severity; this
turns "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H" into 9.8.
"""

from __future__ import annotations

import math

AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
AC = {"L": 0.77, "H": 0.44}
PR_UNCHANGED = {"N": 0.85, "L": 0.62, "H": 0.27}
PR_CHANGED = {"N": 0.85, "L": 0.68, "H": 0.5}
UI = {"N": 0.85, "R": 0.62}
CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _roundup(value: float) -> float:
    """The specification's round-up to one decimal, free of floating-point surprises."""
    whole = round(value * 100_000)
    if whole % 10_000 == 0:
        return whole / 100_000
    return (math.floor(whole / 10_000) + 1) / 10


def base_score(vector: str) -> float | None:
    """The base score of a CVSS 3.0/3.1 vector, or None if it isn't one."""
    parts = vector.strip().split("/")
    if not parts or not parts[0].startswith("CVSS:3."):
        return None
    try:
        m = dict(p.split(":", 1) for p in parts[1:])
        changed = m["S"] == "C"
        iss = 1 - (1 - CIA[m["C"]]) * (1 - CIA[m["I"]]) * (1 - CIA[m["A"]])
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if changed else 6.42 * iss
        pr = (PR_CHANGED if changed else PR_UNCHANGED)[m["PR"]]
        exploitability = 8.22 * AV[m["AV"]] * AC[m["AC"]] * pr * UI[m["UI"]]
    except (KeyError, ValueError):
        return None
    if impact <= 0:
        return 0.0
    total = 1.08 * (impact + exploitability) if changed else impact + exploitability
    return _roundup(min(total, 10))
