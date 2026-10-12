"""Worker process: claims jobs, runs handlers, retries with backoff.

    uv run python -m app.pipeline.worker            # run until interrupted
Each job runs in its own transaction; a failure rolls back that job's work only."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import setup_logging
from app.db.models import Job
from app.pipeline import jobs
from app.providers.base import ModelProvider

log = logging.getLogger(__name__)
Handler = Callable[[Session, dict[str, Any]], dict[str, Any] | None]


def process_group_handler(provider: ModelProvider) -> Handler:
    def run(session: Session, payload: dict[str, Any]) -> dict[str, Any] | None:
        from app.pipeline.process import process_group

        s = process_group(
            session, uuid.UUID(payload["group_id"]), provider, force=bool(payload.get("force"))
        )
        return {
            "bundles": s.bundles, "events": s.events, "observations": s.observations,
            "claims": s.claims, "needs_review": s.needs_review, "failed": s.failed,
            "tokens_in": s.usage.input_tokens, "tokens_out": s.usage.output_tokens,
        }  # fmt: skip

    return run


def run_once(engine: Engine, handlers: dict[str, Handler]) -> bool:
    """Claim and run at most one job. Returns False when nothing was runnable."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as claim_session:
        job = jobs.claim_next(claim_session, list(handlers))
        if job is None:
            claim_session.rollback()
            return False
        job_id, jtype, payload = job.id, job.type, dict(job.payload)
        claim_session.commit()  # PROCESSING is now visible; other workers skip this job
    handler = handlers[jtype]
    try:
        with factory() as work:
            result = handler(work, payload)
            work.commit()
        outcome: tuple[str, dict[str, Any] | None] = ("ok", result)
    except Exception as exc:  # noqa: BLE001 - any handler failure is retried with backoff
        log.warning("job %s failed: %s", job_id, type(exc).__name__)
        outcome = ("error", {"error": f"{type(exc).__name__}: {exc}"})
    with factory() as done:
        j = done.get(Job, job_id)
        if j is not None:
            if outcome[0] == "ok":
                jobs.complete(
                    done, j, outcome[1], needs_review=bool((outcome[1] or {}).get("needs_review"))
                )
            else:
                jobs.fail(done, j, str((outcome[1] or {}).get("error")))
        done.commit()
    return True


def main() -> None:
    from app.db.session import get_engine
    from app.providers.anthropic import AnthropicProvider

    setup_logging()
    engine = get_engine()
    handlers = {"process_group": process_group_handler(AnthropicProvider())}
    log.info("worker started")
    while True:
        if not run_once(engine, handlers):
            time.sleep(2)


if __name__ == "__main__":
    main()
