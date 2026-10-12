"""`make record`: run the pipeline with the REAL model and save every response as a cassette under
tests/cassettes/<task>/<sha256>.json, so tests can replay it offline. Needs ANTHROPIC_API_KEY.

Usage: uv run python scripts/record_cassettes.py "<group name>" [--force]
With no key, tests use hand-written cassettes marked "synthetic": true instead."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from app.core.config import ROOT
from app.core.logging import setup_logging
from app.db.models import Group
from app.db.session import session_scope
from app.pipeline.process import process_group
from app.providers.anthropic import AnthropicProvider
from app.providers.recorded import RecordingProvider


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("group")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "cassettes")
    a = ap.parse_args()
    setup_logging()
    provider = RecordingProvider(AnthropicProvider(), a.out)
    with session_scope() as s:
        g = s.scalar(select(Group).where(Group.name == a.group))
        if g is None:
            raise SystemExit(f"no such group: {a.group}")
        st = process_group(s, g.id, provider, force=a.force)
        print(f"recorded {st.model_calls} responses into {a.out}")


if __name__ == "__main__":
    main()
