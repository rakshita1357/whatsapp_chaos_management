"""Run message understanding for a group.

Usage:
    uv run python scripts/process_group.py "Tata Delight Meter Core Group"          # run now
    uv run python scripts/process_group.py "Tata Delight Meter Core Group" --enqueue  # worker
    uv run python scripts/process_group.py --all [--force]

Needs ANTHROPIC_API_KEY (it calls the configured models). Acknowledgements and bare pointers
never reach the model, so a chat of mostly acknowledgements costs very little."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from app.core.logging import setup_logging
from app.db.models import Group
from app.db.session import session_scope
from app.pipeline import jobs
from app.pipeline.process import process_group
from app.providers.anthropic import AnthropicProvider


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("group", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true", help="reprocess already processed messages")
    ap.add_argument("--enqueue", action="store_true", help="queue a job instead of running now")
    a = ap.parse_args()
    if not a.group and not a.all:
        ap.error("give a group name or --all")
    setup_logging()
    with session_scope() as s:
        q = select(Group) if a.all else select(Group).where(Group.name == a.group)
        groups = list(s.scalars(q))
        if not groups:
            raise SystemExit(f"no such group: {a.group}")
        provider = None if a.enqueue else AnthropicProvider()
        for g in groups:
            if a.enqueue:
                j = jobs.enqueue(s, "process_group", {"group_id": str(g.id), "force": a.force},
                                 dedupe_key=f"process_group:{g.id}")  # fmt: skip
                print(g.name, "queued", j.id)
                continue
            assert provider is not None
            st = process_group(s, g.id, provider, force=a.force)
            keys = (
                "bundles", "skipped", "model_calls", "rule_only", "failed", "events",
                "observations", "claims", "needs_review", "auto_links", "review_links",
            )  # fmt: skip
            summary = {k: getattr(st, k) for k in keys}
            summary["tokens_in"] = st.usage.input_tokens
            summary["tokens_out"] = st.usage.output_tokens
            print(g.name, json.dumps(summary))
            for err in st.errors[:5]:
                print("  error:", err)


if __name__ == "__main__":
    main()
