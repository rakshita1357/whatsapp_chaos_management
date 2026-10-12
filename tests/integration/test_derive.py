from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.state.conflicts import sync_conflicts
from app.state.derive import LADDER, derive_entity, is_installed, level_of
from tests.m3_helpers import seed_site

T = datetime(2026, 10, 9, 10, 0, tzinfo=UTC)


def obs(
    db: Session,
    e: m.Entity,
    dim: str,
    value: str,
    kind: str,
    minutes: int = 0,
    pol: str | None = None,
    day: date = date(2026, 10, 9),
    event: m.Event | None = None,
) -> m.Observation:
    o = m.Observation(entity_id=e.id, dimension=dim, value=value, polarity=pol, observed_at=T + timedelta(minutes=minutes),
                      effective_date=day, source_kind=kind, event_id=event.id if event else None,
                      extractor="t", extractor_version="1")  # fmt: skip
    db.add(o)
    db.flush()
    return o


def st(db: Session, e: m.Entity, dim: str) -> m.EntityState | None:
    return db.scalar(
        select(m.EntityState).where(m.EntityState.entity_id == e.id, m.EntityState.dimension == dim)
    )


def test_ladder_and_installed_counting_rule() -> None:
    assert LADDER == ["not_reported", "planned", "pending_install", "not_installed_reported",
                      "installation_reported", "installed_with_evidence", "verified"]  # fmt: skip
    assert [level_of(v) for v in LADDER] == [0, 1, 2, 3, 4, 5, 6]
    assert level_of("completed") == level_of("installation_reported")  # a sheet claim = reported
    # D6: installed for counting = installation_reported or above
    assert [is_installed(v) for v in LADDER] == [False, False, False, False, True, True, True]
    assert not is_installed(None) and not is_installed("garbage")


def test_precedence_message_beats_sheet_for_installation_and_flags_conflict(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    obs(db, e, "installation", "planned", "workbook_cell", 0)
    obs(db, e, "installation", "installation_reported", "message", -30, "positive")  # OLDER message
    s = derive_entity(db, e.id)
    inst = next(x for x in s if x.dimension == "installation")
    assert (
        inst.value == "installation_reported" and inst.conflict_flag
    )  # message tier outranks sheet
    assert "stale_source" not in sync_conflicts(
        db, e.id
    )  # the sheet is NEWER than the chat: contradiction
    assert sync_conflicts(db, e.id) == ["contradiction"]


def test_sheet_older_than_contradicting_chat_is_stale_source(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    obs(db, e, "installation", "completed", "workbook_cell", 0)
    obs(db, e, "installation", "not_installed_reported", "message", 60, "negative")
    derive_entity(db, e.id)
    assert sync_conflicts(db, e.id) == ["stale_source"]
    # new evidence removes the conflict: the open review item must be retired, not left behind
    obs(db, e, "installation", "completed", "workbook_cell", 120)
    derive_entity(db, e.id)
    sync_conflicts(db, e.id)
    open_items = db.scalars(select(m.ReviewItem).where(m.ReviewItem.status == "open")).all()
    assert [i.kind for i in open_items] == [
        "contradiction"
    ]  # newer sheet now contradicts older chat


def test_readiness_precedence_prefers_the_sheet(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    obs(db, e, "readiness", "not_ready", "message", 60, "negative")
    obs(db, e, "readiness", "ready", "workbook_cell", 0, "positive")
    derive_entity(db, e.id)
    assert st(db, e, "readiness").value == "ready"  # type: ignore[union-attr]  # config: sheet first (provisional D07)


def test_same_source_contradiction_such_as_duplicate_sheet_rows(db: Session) -> None:
    e = seed_site(db, "300A640", "300A640-Sv-Chittorgarh", "Chittorgarh")
    obs(db, e, "readiness", "ready", "workbook_cell", 0, "positive")
    obs(db, e, "readiness", "not_ready", "workbook_cell", 0, "negative")  # same day, same source
    derive_entity(db, e.id)
    assert st(db, e, "readiness").conflict_flag  # type: ignore[union-attr]
    assert sync_conflicts(db, e.id) == ["contradiction"]


def test_only_a_human_can_verify(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    ev = m.Event(type="installation_report", entity_id=e.id, opened_at=T, job_types=[])
    db.add(ev)
    db.flush()
    db.add(m.EventEvidence(event_id=ev.id, attachment_id=_att(db), role="attachment"))
    obs(db, e, "installation", "installation_reported", "message", 0, "positive", event=ev)
    derive_entity(db, e.id)
    assert st(db, e, "installation").value == "installed_with_evidence"  # type: ignore[union-attr]
    obs(db, e, "installation", "verified", "human", 10, "positive")
    derive_entity(db, e.id)
    assert st(db, e, "installation").value == "verified"  # type: ignore[union-attr]


def _att(db: Session) -> object:
    a = m.Attachment(availability="present", mime="image/jpeg")
    db.add(a)
    db.flush()
    return a.id


def test_ready_but_installed_inc50(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    obs(db, e, "readiness", "ready", "workbook_cell", 0, "positive")
    obs(db, e, "installation", "installation_reported", "message", 10, "positive")
    derive_entity(db, e.id)
    assert "ready_but_installed" in sync_conflicts(db, e.id)
    obs(db, e, "readiness", "done", "workbook_cell", 20, "positive")  # sheet now says Done
    derive_entity(db, e.id)
    sync_conflicts(db, e.id)
    assert (
        db.scalar(
            select(m.ReviewItem).where(
                m.ReviewItem.kind == "ready_but_installed", m.ReviewItem.status == "open"
            )
        )
        is None
    )


def test_open_issues_become_a_state_row_and_derive_is_deterministic(db: Session) -> None:
    e = seed_site(db, "300A490", "300A490-Sv-X", "X")
    for t, lc in (
        ("renovation", "open"),
        ("electricity", "acknowledged"),
        ("network", "resolved_reported"),
    ):
        db.add(
            m.Event(
                type="issue",
                entity_id=e.id,
                opened_at=T,
                lifecycle_status=lc,
                payload={"issue_type": t},
                job_types=[],
            )
        )
    obs(db, e, "installation", "planned", "workbook_cell", 0)
    first = [(s.dimension, s.value, s.conflict_flag) for s in derive_entity(db, e.id)]
    second = [(s.dimension, s.value, s.conflict_flag) for s in derive_entity(db, e.id)]
    assert first == second
    assert ("issue", "electricity,renovation", False) in first  # resolved ones are not 'open'
