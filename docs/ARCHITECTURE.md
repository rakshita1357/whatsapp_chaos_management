# Architecture

```
export (txt/md/zip) -> adapter -> MessageEnvelope -> persist -> jobs queue (Postgres, SKIP LOCKED)
   -> fetch/parse attachments -> extract (rules, then model) -> classify -> link -> resolve entities
   -> validate -> commit observations/events -> derive entity_state -> review items
```

- **Connector independence:** everything after `MessageEnvelope` is source-agnostic; a future webhook adapter only emits the same envelope.
- **Layers:** `source_messages`/`attachments`/`workbook_rows` (immutable evidence) -> `observations` (append-only) -> `entity_state` (rebuilt deterministically by `state/derive.py`).
- **Blob store:** `data/blobs/sha256/ab/cd/<hash>`, read-only files, atomic writes, dedup by hash. Derived artefacts live in `data/derived/`. DB rows hold keys only.
- **Time:** instants stored UTC; export wall-clock times are interpreted as IST; business dates are IST (`app/core/time.py`).
- **Queue:** `jobs` table, `FOR UPDATE SKIP LOCKED`, exponential backoff, states per blueprint 5.3.
- **Models:** `ModelProvider` interface (`complete_json`), Anthropic implementation, `RecordedProvider` for tests.
- **Deletions (D2):** hard-delete cascades through observations/events/evidence supported only by the deleted message, then re-derives state; audit row stores ids only.
- **Database in dev:** `docker compose up db`; where Docker is unavailable `scripts/localpg.sh` runs a local Postgres 16.

See `docs/PROGRESS.md` for status and open questions.

## Understanding pipeline (M3)
`process_group` walks a group's messages in file order: build evidence bundles -> rules (`understand/rules.py`) ->
(model, only if rules did not decide) -> `validate_classification` -> resolve entity (`entities/resolve.py`) -> link
thread (`understand/reply_links.py`) -> `commit_bundle` (`state/observations.py`) -> `derive_entities` +
`sync_conflicts`. Observations record what a source said; `entity_state` is rebuilt from them deterministically and
never edited. Uncertainty becomes a `review_item`; the event is stored with `entity_id = NULL` until a human decides.
Jobs: `pipeline/jobs.py` (Postgres queue) and `pipeline/worker.py` (`python -m app.pipeline.worker`).
