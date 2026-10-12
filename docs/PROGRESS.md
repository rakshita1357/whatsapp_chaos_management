# Progress

## M0 Scaffold: done
- uv project, Postgres schema (28 tables) with Alembic baseline `0001`, Makefile, ruff, mypy --strict, pytest.
- Core: config + YAML loaders, IST time utils, PII-safe logging, content-addressed blob store.
- Golden workbook numbers captured as xfail skeleton (`tests/golden/test_workbooks_golden.py`).

## M1 Export ingestion: core done (partial)
Done: 3 parsers (android_native, alt_txt, alt_md), format detection, date-order inference (INC-02/03),
continuation lines, invisible-char normalization, filename-as-caption (INC-13), omitted media, album headers,
logical keys + cross-format merge + second-precision upgrade + `[Forwarded]` and quote enrichment,
D2 hard-delete with hash-only tombstone (old exports cannot resurrect), D3 edits with `message_versions`,
group auto-registration + review items, safe zip, blob-stored media, CLI `scripts/import_chat.py`.
Tests use **synthetic** samples built from the brief; golden `ingestion.*` and INC-xx naming await the real fixtures.
Not yet: `/imports` HTTP endpoint (M5), people.yaml-driven sender resolution, alt-then-native "11 quote links on Tata" check (needs fixtures).

## Open questions for the owner
1. **The repo was empty.** `CLAUDE.md`, `BUILD_PROMPT.md`, `docs/DECISIONS.md`, `docs/KNOWN_INCONSISTENCIES.md`, `fixtures/` (incl. `golden_cases.yaml`), the blueprint PDF and the real `config/*.yaml` were not available. `config/*.yaml` here are **placeholders** inferred from the brief; `CLAUDE.md` was written by me. Please supply the real files; INC-xx and D1-D15 tests cannot be named until then.
2. Docker daemon unavailable in the build sandbox; `make up` falls back to `scripts/localpg.sh`.
3. Branch: work is on `claude/eager-sagan-vqh6gs` (same name as the session branch).
