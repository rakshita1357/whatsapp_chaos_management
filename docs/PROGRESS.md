# Progress

## M0 Scaffold: done
- uv project, Postgres schema (28 tables) with Alembic baseline `0001`, Makefile, ruff, mypy --strict, pytest.
- Core: config + YAML loaders, IST time utils, PII-safe logging, content-addressed blob store.
- Golden workbook numbers captured as xfail skeleton (`tests/golden/test_workbooks_golden.py`).

## M1 Export ingestion: done (against synthetic samples)
- 3 parsers (android_native, alt_txt, alt_md), format detection, date-order inference (INC-02/03), continuation lines,
  invisible-char normalization (INC-04/05), filename-as-caption (INC-13), omitted media, album headers.
- Logical keys, cross-format merge, second-precision upgrade, `[Forwarded]` and quote enrichment, idempotent re-import.
- D2 hard-delete with hash-only tombstone; D3 edits with `message_versions`; group auto-registration + review items.
- People registry (`config/people.yaml`): name-only labels resolve to phone keys before keys are computed.
- Safe zip import; media hashed into the blob store; CLI `scripts/import_chat.py`; `POST /imports` (dev auth, admin only) and `GET /health`.
- Not verifiable until real fixtures arrive: golden `ingestion.*`, named INC-01..16 tests, "11 quote links on Tata".

## M2 Workbooks: done against synthetic workbooks
Built: robust open with pivot-sanitizing fallback (original untouched, `opened_via=sanitized`), `.csv` support,
`.xls` via optional `xlrd` (else a review item), sheet kinds + header-row scan + effective width (INC-22),
header mapping (exact, fuzzy as proposal, unknown kept raw: INC-43), cell normalization (D/M/Y dates, invalid
dates such as `10-10-206`: INC-44, NBSP-stripped codes, digit-only phones/PINs, enum map, `Sr. No.` positional:
INC-46), role inference, family keys (dates and `-1`/`(1)` suffixes removed), versions ordered by posting-message
time not filename (INC-13), same sha256 = same version, master load into dealer/division entities, `New Setup`
provisional entities, duplicate-division conflict review (INC-24), same-site suggestion (INC-23), invalid dealer
code review, version diffs (added/removed/common/changed/format_only/conflict, header renames, day/month-swap
annotation: INC-45), `readiness` and `readiness_listed` observations, rows leaving a READY_LIST observed as
`readiness_listed=false` with reason unknown and linked to chat blockers from the previous 7 days (INC-47),
`ready_delta` (master-ready vs ready list), and workbooks posted in chats are ingested automatically.
Also: configured groups now get their client from `config/groups.yaml` (no `unknown_group_client` review for them).

Assumptions to confirm against the real golden numbers (config in `config/workbooks.yaml`):
- `conflict` = a key duplicated in either version whose rows differ in content; such keys are excluded from
  added/removed/common and from `ready_delta`. Identical duplicate rows collapse silently.
- `format_only` counts rows whose only differences are raw-type/format differences (raw JSON value differs, normalized equal).
- A changed row is classified timeline/dispatch by the FIRST group (config order) containing a changed column.
- Unknown-header columns are stored raw but not diffed. `row_key` for `New Setup` placeholders is null (not diffed).
- Not done yet: 8.7 reconcile with chat (`stale_source`, `contradiction`, `ready_but_installed`), because it
  needs chat observations and `state/derive.py` (M3). Real master pivot crash is unverified (no fixture): the
  sanitizer is tested on a synthetic workbook with injected pivot parts.
- The golden skeleton in `tests/golden/test_workbooks_golden.py` stays xfail until the real fixtures exist.

### M2 follow-up: real Dealer List headers (supplied by the owner's colleague via screenshot/paste)
Headers: Date, Dealer Name, Division Code, City, Hub Locations, SPOC - Name, SPOC - Contact, Address1-3, Pincode,
Contacted, Readiness, Remark, Installation Planned Date, Installation Status, Phase, Expecting Timeline for
Installation, Dispatch Date, Status, Albersons Remakrs (sic). All 21 map exactly (test: `test_real_dealer_list_headers.py`).
- There is no Dealer Code column; the dealer code is derived from the Division Code prefix (3000060-Sv&Pa-... -> 3000060).
- Dealer-code pattern widened to `30` + digit + [0-9A-Z] + 3 digits (3085780 and 3083274 are valid; 300A42 is not).
- Assumptions to confirm: "Status" (after Dispatch Date) = dispatch status; "Date" = a record/update date;
  the distinct values of Readiness, Installation Status, Status and Contacted (only "Ready To Deploy" is mapped; others
  surface as `unmapped_status_value` review items); which columns count as timeline vs dispatch in diffs.
- Sheets `Summary` and `implementation Plan` headers not yet seen.

### Assumptions adopted by the owner's colleague ("use your assumptions")
`Status` = dispatch status; `Date` = row/update date; timeline columns = Installation Planned Date + Expecting
Timeline for Installation; dispatch columns = Dispatch Date + Status. Status value lists still to be read from the real file
(`scripts/inspect_workbook.py`). Bug fixed: a master carrying an `implementation Plan` sheet was classified
IMPLEMENTATION_PLAN; now only the primary sheet (preferred: `Dealer List`) decides the role.
Real master confirmed to need the pivot sanitizer (`opened_via: sanitized`).

### Findings from the real master (via scripts/inspect_workbook.py output)
- Sheets: `Summary` (visible pivot), `implementation Plan` (222 rows, all "Ready To Deploy", 21 cols, no Hub/Date/Status),
  `Dealer List` (1180 rows, 25 cols incl. Sr. No., New Region, Dealer Code), hidden `Sheet1` (pivot) and `Sheet2` (helper, 42 cols).
- Plain open fails with `TypeError: Nested.from_tree() ...` (pivot cache), so the sanitizer is required: confirmed on the real file.
- Real values now mapped in `config/status_map.yaml`: Readiness {Ready To Deploy, Done, Pending}; Installation Status
  {Completed, Pending to installed, Planned}; Status {Pending, ISSUE, NOT Dispatched, PHASE-1}; Contacted {Yes/yes}.
  `standalone bodyshop` in Contacted and the bare `Done` meaning are flagged/assumed (assumption: Done = work done).
- Workbook `Installation Status` is recorded as an `installation` observation (workbook claim, never "verified").
- Data-quality notes (not yet normalised): State has `Orissa`/`Odisha` and `J & K`/`Jammu & Kashmir`;
  Expecting Timeline has free text ("Will be done in this week", "Will be completed in 2 days") kept as `__text`
  for ETA parsing (INC-59, M3). `Date` is blank for 920 of 1179 rows.
- Fixed: `scripts/import_workbook.py --replace` was documented but its flag was never defined (silent edit failure);
  now defined, and it also removes the old import's open review items.

### Performance fix (real master import)
The first real import against Neon was very slow: ~9,400 queries for 1,180 rows (12 s locally, many minutes over the
network). Entities/identifiers are now cached in memory and written in batches, and `entity_id` is set with one
executemany: ~20 queries / 3.5 s locally. Guarded by a query-count test.

## M3 Understanding + state: done against synthetic chats and a scripted model (live model untested)
Built: model provider layer (`ModelProvider`; `AnthropicProvider` using structured JSON output; `RecordedProvider` /
`RecordingProvider` cassettes), rules (codes from config, acknowledgement lexicon, pointers, status keywords incl.
Hinglish, mentions, aggregate hints), model classification with a strict schema + Hinglish few-shots, a validator that
enforces the invariants (acks never change status, aggregates never create events, status needs a text span),
evidence bundles (album / image + short caption / 8 images + pointer), reply-link scoring with weights in
`config/reply_weights.yaml`, entity resolution (Tata division/dealer/town; Maruti company+channel+town outlets),
the commit step (events, observations, aggregate claims, follow-up windows from ETA text, issue lifecycle:
ack moves open -> acknowledged only), derived state (installation ladder, D6 counting, precedence, conflict flags),
conflicts (stale_source, contradiction, ready_but_installed: INC-50), review items, and a Postgres job queue
(SKIP LOCKED, exponential backoff, stale-lock release) with a worker. Re-running a bundle replaces only its own outputs.

Golden constraints (7.3), on synthetic scenarios modelled on the brief: native-only "Tv installed" is never auto-linked
(goes to review); "Tv installed in lounge" ranks Himatnagar first (review) or auto-links to it when the sender was
assigned and acknowledged; renovation ranks Chokidhani first, never Himatnagar; a 2,000-case randomized check proves no
link is automatic without score >= 0.80 AND margin >= 0.25. Auto-link precision on the synthetic cases: 1.0, wrong
auto-links: 0. **These numbers are NOT the real-fixture precision/recall the brief asks for; weights are untuned.**

Decisions I made that you may want to change (all in config or one function):
- A mention on a NOT-done report ("NO TV INSTALLED @Ravi check") assigns that person; on a positive report it does not.
- An acknowledgement links to the immediately preceding message from someone else within 10 minutes; a bare pointer links
  to the immediately preceding message. Both are marked `inferred` (they never change a status).
- Source precedence per dimension is in `config/precedence.yaml` (provisional D07: chat beats the sheet for
  installation; the sheet beats chat for readiness). "installed_with_evidence" currently = any attachment on the
  bundle; M4 will require real image evidence. "verified" only comes from a human action (D08 still open).
- Maruti outlets are created provisionally from chat (no master list exists).

Not verified / not done: the AnthropicProvider has only been tested with a fake client (no API key in this sandbox), so
real-model behaviour, cost and the optional server-side refusal fallback are unproven. Not built yet: vision/attachment
evidence (M4), review actions that apply a decision (M5), category proposals (M5), assistant (M6). New migrations 0002
(events.payload, review dedupe key) and 0003 (messages.source_line, needed to keep file order within a minute).

## Open questions for the owner
0. **Owner decisions needed for M3:** D07 (which source wins per dimension), D08 (what "verified" means), and whether a mention on a status message should count as an assignment.
1. **The repo was empty.** (M2 placeholders added: `config/header_aliases.yaml`, `status_map.yaml`, `workbooks.yaml`.) `CLAUDE.md`, `BUILD_PROMPT.md`, `docs/DECISIONS.md`, `docs/KNOWN_INCONSISTENCIES.md`, `fixtures/` (incl. `golden_cases.yaml`), the blueprint PDF and the real `config/*.yaml` were not available. `config/*.yaml` here are **placeholders** inferred from the brief; `CLAUDE.md` was written by me. Please supply the real files; INC-xx and D1-D15 tests cannot be named until then.
2. Docker daemon unavailable in the build sandbox; `make up` falls back to `scripts/localpg.sh`.
3. Branch: work is on `claude/eager-sagan-vqh6gs` (same name as the session branch).
