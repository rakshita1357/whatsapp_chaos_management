# Progress

## M0 Scaffold: done
- uv project, Postgres schema (28 tables) with Alembic baseline `0001`, Makefile, ruff, mypy --strict, pytest.
- Core: config + YAML loaders, IST time utils, PII-safe logging, content-addressed blob store.
- Golden workbook numbers captured as xfail skeleton (`tests/golden/test_workbooks_golden.py`).

## Open questions for the owner
1. **The repo was empty.** `CLAUDE.md`, `BUILD_PROMPT.md`, `docs/DECISIONS.md`, `docs/KNOWN_INCONSISTENCIES.md`, `fixtures/` (incl. `golden_cases.yaml`), the blueprint PDF and the real `config/*.yaml` were not available. `config/*.yaml` here are **placeholders** inferred from the brief; `CLAUDE.md` was written by me. Please supply the real files; INC-xx and D1-D15 tests cannot be named until then.
2. Docker daemon unavailable in the build sandbox; `make up` falls back to `scripts/localpg.sh`.
3. Branch: work is on `claude/eager-sagan-vqh6gs` (same name as the session branch).
