"""Cell normalization (build brief 8.3): raw is always kept; normalized may be None."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from app.workbooks.settings import WorkbookConfig

_DATE_TEXT = re.compile(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})\s*$")
_ISO_TEXT = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*$")


@dataclass
class Cell:
    raw: Any  # JSON-safe
    type: str  # empty | str | number | bool | datetime | date
    normalized: str | None
    parse_status: str  # ok | empty | invalid | text | unmapped
    normalized_text: str | None = None


def json_raw(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        return v.replace(" ", " ")
    return v


def _type_of(v: Any) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        return "empty"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float, Decimal)):
        return "number"
    if isinstance(v, datetime):
        return "datetime"
    if isinstance(v, date):
        return "date"
    return "str"


def code_str(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"[\s ]+", " ", str(v)).strip()


def division_key(v: Any) -> str:
    """Division key = all whitespace removed + casefold."""
    return re.sub(r"[\s ]+", "", str(v)).casefold()


def digits_only(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\D", "", str(v))


def parse_date_text(s: str, order: str) -> date | None:
    m = _ISO_TEXT.match(s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = _DATE_TEXT.match(s)
    if not m:
        return None
    a, b, y = int(m[1]), int(m[2]), m[3]
    year = int(y) + 2000 if len(y) == 2 else int(y)
    day, month = (a, b) if order == "DMY" else (b, a)
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    return d if 2000 <= d.year <= 2100 else None


def normalize_cell(value: Any, canonical: str | None, role: str, cfg: WorkbookConfig) -> Cell:
    t = _type_of(value)
    raw = json_raw(value)
    if t == "empty":
        return Cell(raw, t, None, "empty")
    ftype = cfg.field_type(canonical) if canonical else "text"
    order = str(cfg.settings.get("date_order", "DMY"))
    if ftype == "positional":
        return Cell(raw, t, None, "ok")  # stored, never a key, never diffed
    if ftype == "date":
        if isinstance(value, datetime):
            return Cell(raw, t, value.date().isoformat(), "ok")
        if isinstance(value, date):
            return Cell(raw, t, value.isoformat(), "ok")
        s = str(value).strip()
        parsed = parse_date_text(s, order)
        if parsed:
            return Cell(raw, t, parsed.isoformat(), "ok")
        if re.fullmatch(r"[\d/.\-\s]+", s):  # looks like a date but is not one: INC-44 style
            return Cell(raw, t, None, "invalid")
        return Cell(raw, t, None, "text", normalized_text=s)
    if ftype == "code":
        return Cell(raw, t, code_str(value), "ok")
    if ftype in ("phone", "pin"):
        d = digits_only(value)
        return Cell(raw, t, d or None, "ok" if d else "invalid")
    if ftype == "enum" and canonical:
        s = str(value).strip()
        hit = cfg.enum_lookup(role, canonical, s)
        if hit is None:
            return Cell(raw, t, None, "unmapped", normalized_text=s)
        return Cell(raw, t, hit, "ok")
    return Cell(raw, t, re.sub(r"\s+", " ", str(value)).strip() if t == "str" else str(raw), "ok")
