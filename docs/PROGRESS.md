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

## Open questions for the owner
1. **The repo was empty.** (M2 placeholders added: `config/header_aliases.yaml`, `status_map.yaml`, `workbooks.yaml`.) `CLAUDE.md`, `BUILD_PROMPT.md`, `docs/DECISIONS.md`, `docs/KNOWN_INCONSISTENCIES.md`, `fixtures/` (incl. `golden_cases.yaml`), the blueprint PDF and the real `config/*.yaml` were not available. `config/*.yaml` here are **placeholders** inferred from the brief; `CLAUDE.md` was written by me. Please supply the real files; INC-xx and D1-D15 tests cannot be named until then.
2. Docker daemon unavailable in the build sandbox; `make up` falls back to `scripts/localpg.sh`.
3. Branch: work is on `claude/eager-sagan-vqh6gs` (same name as the session branch).
