"""Postgres job queue: `SELECT ... FOR UPDATE SKIP LOCKED`, exponential backoff, blueprint 5.3
states. No Redis. A job is claimed (PROCESSING) in its own short transaction, so others skip it."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.time import now_utc
from app.db.models import Job

STATES = (
    "RECEIVED", "QUEUED", "PROCESSING", "PROCESSED", "NEEDS_REVIEW",
    "FAILED_RETRYABLE", "FAILED_FINAL", "IGNORED_FOR_OPERATIONS",
)  # fmt: skip
CLAIMABLE = ("QUEUED", "FAILED_RETRYABLE")
BASE_BACKOFF_S = 5
MAX_BACKOFF_S = 3600


def backoff_seconds(attempts: int) -> int:
    """5s, 10s, 20s, 40s ... capped at one hour."""
    return int(min(BASE_BACKOFF_S * 2 ** max(attempts - 1, 0), MAX_BACKOFF_S))


def enqueue(
    session: Session,
    type_: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> Job:
    """Idempotent per dedupe_key while a job with that key is not finished."""
    if dedupe_key:
        existing = session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing is not None:
            if existing.state in ("PROCESSED", "FAILED_FINAL", "IGNORED_FOR_OPERATIONS"):
                existing.state, existing.attempts, existing.last_error = "QUEUED", 0, None
                existing.run_after = run_after or now_utc()
            return existing
    job = Job(
        id=uuid.uuid4(), type=type_, payload=payload, dedupe_key=dedupe_key, state="QUEUED",
        max_attempts=max_attempts, run_after=run_after or now_utc(),
    )  # fmt: skip
    session.add(job)
    session.flush()
    return job


def claim_next(session: Session, types: list[str] | None = None) -> Job | None:
    q = (
        select(Job)
        .where(Job.state.in_(CLAIMABLE), Job.run_after <= now_utc())
        .order_by(Job.run_after, Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if types:
        q = q.where(Job.type.in_(types))
    job = session.scalar(q)
    if job is None:
        return None
    job.state, job.attempts, job.locked_at = "PROCESSING", job.attempts + 1, now_utc()
    session.flush()
    return job


def complete(
    session: Session, job: Job, result: dict[str, Any] | None = None, *, needs_review: bool = False
) -> None:
    job.state = "NEEDS_REVIEW" if needs_review else "PROCESSED"
    job.result, job.locked_at, job.last_error = result, None, None
    session.flush()


def fail(session: Session, job: Job, error: str) -> None:
    job.last_error = error[:2000]
    job.locked_at = None
    if job.attempts >= job.max_attempts:
        job.state = "FAILED_FINAL"
    else:
        job.state = "FAILED_RETRYABLE"
        job.run_after = now_utc() + timedelta(seconds=backoff_seconds(job.attempts))
    session.flush()


def release_stale(session: Session, older_than_s: int = 900) -> int:
    """A worker that died mid-job leaves PROCESSING rows; make them retryable."""
    cutoff = now_utc() - timedelta(seconds=older_than_s)
    res = session.execute(
        update(Job)
        .where(Job.state == "PROCESSING", Job.locked_at < cutoff)
        .values(state="FAILED_RETRYABLE", last_error="worker lost (stale lock)", locked_at=None)
    )
    return int(res.rowcount or 0)  # type: ignore[attr-defined]


def queue_depth(session: Session) -> dict[str, int]:
    from sqlalchemy import func

    rows = session.execute(select(Job.state, func.count()).group_by(Job.state)).all()
    return {s: int(n) for s, n in rows}
