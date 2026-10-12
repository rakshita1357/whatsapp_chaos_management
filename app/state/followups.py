"""ETA text -> follow-up window in IST dates (INC-59); issue age with no real response (INC-60)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.core.time import ist_date

_NUM = r"(\d{1,3})"
_UNIT_DAYS = r"(?:days?|din|dino|दिन)"
_UNIT_WEEKS = r"(?:weeks?|hafta|hafte|हफ्ते)"
_RANGE_DAYS = re.compile(rf"{_NUM}\s*(?:-|–|to|se)\s*{_NUM}\s*{_UNIT_DAYS}", re.IGNORECASE)
_RANGE_WEEKS = re.compile(rf"{_NUM}\s*(?:-|–|to)\s*{_NUM}\s*{_UNIT_WEEKS}", re.IGNORECASE)
_N_DAYS = re.compile(
    rf"(?:in|after|within|aprox|approx|around)?\s*{_NUM}\s*{_UNIT_DAYS}", re.IGNORECASE
)
_N_WEEKS = re.compile(rf"{_NUM}\s*{_UNIT_WEEKS}", re.IGNORECASE)
_DAY_MONTH = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b")
_MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, 1)}
_NAMED_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\b")


def parse_eta(text: str | None, base: date) -> tuple[date, date] | None:
    """Return (from, to) IST dates, or None when the wording gives no usable time."""
    if not text:
        return None
    t = text.strip().casefold()
    if (m := _RANGE_DAYS.search(t)) or (m := _RANGE_WEEKS.search(t)):
        a, b = int(m.group(1)), int(m.group(2))
        k = 7 if _RANGE_WEEKS.search(t) and not _RANGE_DAYS.search(t) else 1
        return base + timedelta(days=min(a, b) * k), base + timedelta(days=max(a, b) * k)
    if re.search(r"day after tomorrow|parso|parson", t):
        d = base + timedelta(days=2)
        return d, d
    if re.search(r"\btomorrow\b|\bkal\b|\bcl\b", t):
        d = base + timedelta(days=1)
        return d, d
    if re.search(r"\btoday\b|\baaj\b|\bsame day\b", t):
        return base, base
    if "next week" in t or "agle hafte" in t or "agle week" in t:
        monday = base + timedelta(days=7 - base.weekday())
        return monday, monday + timedelta(days=6)
    if "this week" in t or "is hafte" in t or "is week" in t:
        return base, base + timedelta(days=6 - base.weekday())
    if m := _N_WEEKS.search(t):
        d = base + timedelta(days=int(m.group(1)) * 7)
        return d, d
    if m := _N_DAYS.search(t):
        d = base + timedelta(days=int(m.group(1)))
        return d, d
    if (m := _NAMED_MONTH.search(t)) and m.group(2)[:3] in _MONTHS:
        named = _safe_date(base.year, _MONTHS[m.group(2)[:3]], int(m.group(1)), base)
        return (named, named) if named else None
    if m := _DAY_MONTH.search(t):
        day, month = int(m.group(1)), int(m.group(2))  # Indian convention: D/M
        year = (
            int(m.group(3)) + (2000 if m.group(3) and len(m.group(3)) == 2 else 0)
            if m.group(3)
            else base.year
        )
        parsed = _safe_date(year, month, day, base)
        return (parsed, parsed) if parsed else None
    return None


def _safe_date(year: int, month: int, day: int, base: date) -> date | None:
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    if d < base - timedelta(days=30):  # a day/month already long past means next year
        try:
            d = date(year + 1, month, day)
        except ValueError:
            return None
    return d


def eta_for_message(eta_text: str | None, sent_at: datetime) -> tuple[date, date] | None:
    return parse_eta(eta_text, ist_date(sent_at))


def issue_age_days(opened_at: datetime, now: datetime) -> int:
    """Whole IST days an issue has been open (INC-60: report age for issues nobody answered)."""
    return (ist_date(now) - ist_date(opened_at)).days


def next_lifecycle_on_ack(status: str | None) -> str | None:
    """An acknowledgement moves open -> acknowledged and never further."""
    return "acknowledged" if status == "open" else status
