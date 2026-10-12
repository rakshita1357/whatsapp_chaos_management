"""Review queue helpers. Items are idempotent per dedupe_key while open."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ReviewItem

KINDS = {
    "reply_link_ambiguous", "entity_match_ambiguous", "division_ambiguity",
    "duplicate_division_conflict", "possible_duplicate_entity", "possible_duplicate_attachment",
    "invalid_value", "unknown_header", "unmapped_status_value", "date_order_ambiguous",
    "contradiction", "stale_source", "new_category_proposal", "unknown_group_client",
    "low_confidence_extraction", "ready_but_installed",
}  # fmt: skip


def raise_review(
    session: Session,
    kind: str,
    *,
    dedupe_key: str,
    group_id: uuid.UUID | None = None,
    subject_refs: dict[str, Any] | None = None,
    candidates: list[Any] | None = None,
    proposed_value: dict[str, Any] | None = None,
    note: str | None = None,
    severity: str = "medium",
) -> ReviewItem:
    """Create a review item unless an open one with the same dedupe_key exists."""
    existing = session.scalar(
        select(ReviewItem).where(
            ReviewItem.dedupe_key == dedupe_key,
            ReviewItem.status == "open",
            ReviewItem.kind == kind,
        )
    )
    if existing is not None:
        existing.candidates = candidates or existing.candidates
        return existing
    item = ReviewItem(
        id=uuid.uuid4(),
        kind=kind,
        dedupe_key=dedupe_key,
        severity=severity,
        group_id=group_id,
        subject_refs=subject_refs or {},
        candidates=candidates or [],
        proposed_value=proposed_value,
        note=note,
    )
    session.add(item)
    return item


def clear_open_items(session: Session, dedupe_prefix: str) -> int:
    """Remove OPEN items whose key starts with the prefix (decided items are history, kept)."""
    res = session.execute(
        delete(ReviewItem).where(
            ReviewItem.dedupe_key.like(dedupe_prefix + "%"), ReviewItem.status == "open"
        )
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]
