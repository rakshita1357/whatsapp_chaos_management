"""IST helpers. All stored instants are tz-aware UTC; business dates are IST."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; refusing to guess a timezone")
    return dt.astimezone(IST)


def ist_date(dt: datetime) -> date:
    return to_ist(dt).date()


def from_ist_naive(dt: datetime) -> datetime:
    """Interpret a naive wall-clock datetime (as in WhatsApp exports) as IST, return UTC."""
    if dt.tzinfo is not None:
        raise ValueError("expected a naive datetime")
    return dt.replace(tzinfo=IST).astimezone(UTC)


def ist_day_bounds(d: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, as UTC instants."""
    start = datetime(d.year, d.month, d.day, tzinfo=IST)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def truncate_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def yesterday_ist(now: datetime) -> date:
    return ist_date(now) - timedelta(days=1)
