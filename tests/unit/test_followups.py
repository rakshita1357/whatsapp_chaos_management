from datetime import UTC, date, datetime

import pytest

from app.state.followups import eta_for_message, issue_age_days, next_lifecycle_on_ack, parse_eta

B = date(2026, 10, 9)  # a Friday


@pytest.mark.parametrize(
    ("text", "expect"),
    [
        ("2-3 days", (date(2026, 10, 11), date(2026, 10, 12))),
        ("2 to 3 days", (date(2026, 10, 11), date(2026, 10, 12))),
        ("2-3 din", (date(2026, 10, 11), date(2026, 10, 12))),
        ("in 5 days", (date(2026, 10, 14), date(2026, 10, 14))),
        ("after 10 days", (date(2026, 10, 19), date(2026, 10, 19))),
        ("tomorrow", (date(2026, 10, 10), date(2026, 10, 10))),
        ("kal tak ho jayega", (date(2026, 10, 10), date(2026, 10, 10))),
        ("parso", (date(2026, 10, 11), date(2026, 10, 11))),
        ("today", (B, B)),
        ("this week", (B, date(2026, 10, 11))),
        ("next week", (date(2026, 10, 12), date(2026, 10, 18))),
        ("1-2 weeks", (date(2026, 10, 16), date(2026, 10, 23))),
        ("2 weeks", (date(2026, 10, 23), date(2026, 10, 23))),
        ("by 15 Oct", (date(2026, 10, 15), date(2026, 10, 15))),
        ("20/10", (date(2026, 10, 20), date(2026, 10, 20))),
        ("20/10/2026", (date(2026, 10, 20), date(2026, 10, 20))),
    ],
)
def test_parse_eta(text: str, expect: tuple[date, date]) -> None:
    assert parse_eta(text, B) == expect


@pytest.mark.parametrize("text", [None, "", "soon", "will let you know", "30/02"])
def test_unparseable_gives_none(text: str | None) -> None:
    assert parse_eta(text, B) is None


def test_eta_uses_the_ist_date_of_the_message() -> None:
    sent = datetime(2026, 10, 9, 20, 0, tzinfo=UTC)  # already 10 Oct in IST (01:30)
    assert eta_for_message("tomorrow", sent) == (date(2026, 10, 11), date(2026, 10, 11))


def test_issue_age_and_ack_lifecycle() -> None:
    opened = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)
    assert issue_age_days(opened, datetime(2026, 10, 12, 10, 0, tzinfo=UTC)) == 3
    assert next_lifecycle_on_ack("open") == "acknowledged"
    for s in ("acknowledged", "investigating", "waiting_external", "resolved_reported", None):
        assert next_lifecycle_on_ack(s) == s  # an ack never advances beyond acknowledged
