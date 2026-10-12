"""Date-order inference (INC-02)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Order = Literal["DMY", "MDY"]


@dataclass
class DateOrderResult:
    order: Order | None
    needs_review: bool
    evidence: dict[str, Any] = field(default_factory=dict)


def infer_date_order(
    pairs: list[tuple[int, int]],
    family_default: Order,
    check_monotonic: Any,
) -> DateOrderResult:
    """pairs: (first, second) fields of every dated line. `check_monotonic(order) -> bool` parses
    non-system message timestamps under that order and reports whether they never decrease."""
    first_gt12 = any(a > 12 for a, _ in pairs)
    second_gt12 = any(b > 12 for _, b in pairs)
    ev: dict[str, Any] = {
        "dates_seen": len(pairs),
        "first_field_gt_12": first_gt12,
        "second_field_gt_12": second_gt12,
        "family_default": family_default,
    }
    if first_gt12 and second_gt12:
        ev["reason"] = "contradictory: both fields exceed 12"
        return DateOrderResult(None, True, ev)
    if first_gt12:
        ev["reason"] = "first field > 12"
        return DateOrderResult("DMY", False, ev)
    if second_gt12:
        ev["reason"] = "second field > 12"
        return DateOrderResult("MDY", False, ev)
    ambiguous_all = all(a == b for a, b in pairs) if pairs else True
    ok = bool(check_monotonic(family_default)) and bool(pairs)
    ev["monotonic_under_default"] = ok
    ev["all_dates_symmetric"] = ambiguous_all
    if ok and not ambiguous_all:
        ev["reason"] = "no day>12; family default is monotonic"
        return DateOrderResult(family_default, False, ev)
    ev["reason"] = "no evidence either way" if ok else "family default breaks monotonic order"
    return DateOrderResult(None, True, ev)


def build_dt(y: int, a: int, b: int, order: Order, hh: int, mm: int, ss: int = 0) -> datetime:
    day, month = (a, b) if order == "DMY" else (b, a)
    if y < 100:
        y += 2000
    return datetime(y, month, day, hh, mm, ss)
