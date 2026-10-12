from datetime import UTC, date, datetime

import pytest

from app.core.time import from_ist_naive, ist_date, ist_day_bounds, to_ist, yesterday_ist


def test_ist_conversion_crosses_midnight() -> None:
    utc = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # 01:30 IST next day
    assert ist_date(utc) == date(2026, 10, 10)
    assert to_ist(utc).hour == 1 and to_ist(utc).minute == 30


def test_from_ist_naive_roundtrip() -> None:
    assert from_ist_naive(datetime(2026, 10, 9, 13, 27)) == datetime(2026, 10, 9, 7, 57, tzinfo=UTC)


def test_naive_refused() -> None:
    with pytest.raises(ValueError):
        to_ist(datetime(2026, 1, 1))


def test_day_bounds_and_yesterday() -> None:
    s, e = ist_day_bounds(date(2026, 10, 9))
    assert (e - s).total_seconds() == 86400
    assert yesterday_ist(datetime(2026, 10, 12, 0, 10, tzinfo=UTC)) == date(2026, 10, 11)
