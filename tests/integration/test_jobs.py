from datetime import timedelta
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.time import now_utc
from app.db.models import Job
from app.pipeline import jobs
from app.pipeline.worker import run_once


def test_backoff_is_exponential_and_capped() -> None:
    assert [jobs.backoff_seconds(n) for n in (1, 2, 3, 4)] == [5, 10, 20, 40]
    assert jobs.backoff_seconds(30) == jobs.MAX_BACKOFF_S


def test_enqueue_is_idempotent_per_dedupe_key(db: Session) -> None:
    a = jobs.enqueue(db, "process_group", {"g": 1}, dedupe_key="group:1")
    b = jobs.enqueue(db, "process_group", {"g": 1}, dedupe_key="group:1")
    assert a.id == b.id
    jobs.claim_next(db)
    jobs.complete(db, a)
    c = jobs.enqueue(db, "process_group", {"g": 1}, dedupe_key="group:1")
    assert c.id == a.id and c.state == "QUEUED"  # a finished job can be requeued, not duplicated


def test_claim_uses_skip_locked_so_workers_never_share_a_job(engine: Engine, db: Session) -> None:
    j1 = jobs.enqueue(db, "t", {"n": 1})
    j2 = jobs.enqueue(db, "t", {"n": 2})
    db.commit()
    with Session(engine) as w1, Session(engine) as w2:
        c1 = jobs.claim_next(w1)  # holds the row lock: w1 has not committed
        c2 = jobs.claim_next(w2)  # must skip it, not wait and not take the same row
        c3 = jobs.claim_next(w2)
        assert c1 is not None and c2 is not None and c1.id != c2.id
        assert {c1.id, c2.id} == {j1.id, j2.id} and c3 is None
        w1.rollback()
        w2.rollback()


def test_failure_backs_off_then_goes_final(db: Session) -> None:
    j = jobs.enqueue(db, "t", {}, max_attempts=3)
    for attempt in (1, 2):
        j.run_after = now_utc() - timedelta(seconds=1)
        got = jobs.claim_next(db)
        assert got is not None and got.attempts == attempt
        jobs.fail(db, got, "boom")
        assert got.state == "FAILED_RETRYABLE"
        assert got.run_after > now_utc() + timedelta(seconds=jobs.backoff_seconds(attempt) - 2)
        assert jobs.claim_next(db) is None  # not runnable until the backoff passes
    j.run_after = now_utc() - timedelta(seconds=1)
    got = jobs.claim_next(db)
    assert got is not None
    jobs.fail(db, got, "boom again")
    assert got.state == "FAILED_FINAL" and got.last_error == "boom again"
    assert jobs.queue_depth(db)["FAILED_FINAL"] == 1


def test_stale_processing_jobs_become_retryable(db: Session) -> None:
    j = jobs.enqueue(db, "t", {})
    got = jobs.claim_next(db)
    assert got is not None and got.state == "PROCESSING"
    got.locked_at = now_utc() - timedelta(hours=2)
    assert jobs.release_stale(db, 900) == 1
    db.refresh(j)
    assert j.state == "FAILED_RETRYABLE"


def test_worker_runs_handlers_and_records_results_and_failures(engine: Engine, db: Session) -> None:
    ok = jobs.enqueue(db, "good", {"x": 2})
    bad = jobs.enqueue(db, "bad", {})
    db.commit()
    calls: list[dict[str, Any]] = []

    def good(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"doubled": payload["x"] * 2}

    def bad_handler(session: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
        raise RuntimeError("model unavailable")

    handlers = {"good": good, "bad": bad_handler}
    assert run_once(engine, handlers) and run_once(engine, handlers)
    assert run_once(engine, handlers) is False  # nothing else runnable (the failure is backing off)
    with Session(engine) as s:
        a, b = s.get(Job, ok.id), s.get(Job, bad.id)
        assert a is not None and b is not None
        assert a.state == "PROCESSED" and a.result == {"doubled": 4} and calls == [{"x": 2}]
        assert b.state == "FAILED_RETRYABLE" and "model unavailable" in (b.last_error or "")
        assert b.attempts == 1 and b.run_after > now_utc()
        assert s.scalar(select(Job.id).where(Job.state == "QUEUED")) is None
