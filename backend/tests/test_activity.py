"""The day's activity: outages, short drops counted, many at once as one, laptops online."""

from datetime import UTC, datetime, timedelta

from kastellan.history import Row
from kastellan.stats import activity

T0 = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=10)
SHORT = timedelta(minutes=3)


def rows(device: str, ups: list[tuple[int, bool]], check: str = "ping") -> list[Row]:
    """Results at minutes after T0."""
    return [
        Row(T0 + timedelta(minutes=m), device, check, up, None, "" if up else "no reply") for m, up in ups
    ]


def run(all_rows: list[Row], on: dict[str, bool]) -> list[dict]:
    return activity(sorted(all_rows, key=lambda r: r.time), on, NOW, SHORT, SHORT)


def test_an_outage_and_short_drops() -> None:
    r = rows(
        "nas", [(0, True), (60, False), (72, True), (100, False), (101, True), (200, False), (201, True)]
    )
    out = run(r, {"nas": True})
    kinds = [(e["kind"], e.get("count")) for e in out]
    assert ("blips", 2) in kinds
    outage = next(e for e in out if e["kind"] == "outage")
    assert (outage["end"] - outage["start"]) == timedelta(minutes=12) and outage["detail"] == "no reply"


def test_still_down_is_on_top() -> None:
    out = run(
        rows("nas", [(0, True), (590, False), (599, False)])
        + rows("pi", [(0, True), (10, False), (20, True)]),
        {"nas": True, "pi": True},
    )
    assert out[0]["kind"] == "outage" and out[0]["device"] == "nas" and out[0]["end"] is None


def test_many_at_once_is_one_entry() -> None:
    names = [f"d{i}" for i in range(6)]
    r = [x for n in names for x in rows(n, [(0, True), (30, False), (35, True)])]
    r += rows("nas", [(0, True), (500, False), (520, True)])
    r += rows("pi", [(0, True), (33, False), (40, True)])  # starts a little later: the same trouble
    r += rows("laptop", [(0, True), (30, False), (36, True), (300, False)])
    out = run(r, dict.fromkeys([*names, "nas", "pi"], True) | {"laptop": False})
    together = [e for e in out if e["kind"] == "together"]
    assert len(together) == 1 and together[0]["count"] == 7  # the laptop too
    assert [e["device"] for e in out if e["kind"] == "outage"] == ["nas"]
    # The laptop was online all along, until 300: not split by the hub's trouble.
    assert [(e["start"], e["end"]) for e in out if e["kind"] == "online"] == [
        (None, T0 + timedelta(minutes=300))
    ]


def test_a_laptop_is_online_not_up_and_down() -> None:
    r = rows("laptop", [(0, False), (60, True), (120, False), (121, True), (300, False)])
    out = run(r, {"laptop": False})
    assert [(e["kind"], e["start"], e["end"]) for e in out] == [
        ("online", T0 + timedelta(minutes=60), T0 + timedelta(minutes=300))  # the short drop merged
    ]
    assert run(rows("laptop", [(0, True), (500, True)]), {"laptop": False}) == []  # on all the time
