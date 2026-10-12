# What the owner needs to do

Status: after M1. Tick items off as you go.

## A. Needed now to run what exists (M0 + M1)
1. **Install tools** on your machine: Python 3.12, `uv`, Docker (Desktop or Engine), `make`.
2. **Create your env file.** From the repo root: `cp .env.example .env`, then edit `.env`:
   - `WCM_DATABASE_URL=postgresql+psycopg://wcm:wcm@localhost:5432/wcm` (matches `docker-compose.yml`; change user/password in both places if you want different ones)
   - `WCM_DEV_AUTH=1` (required, otherwise `POST /imports` answers 401)
   - `ANTHROPIC_API_KEY=` leave empty for now (see section C)
   `.env` is git-ignored; never commit it.
3. **Start the database and create the tables:**
   `uv sync && make up && make migrate && make test`
   If Docker is not available, `make up` falls back to a local Postgres 16 via `scripts/localpg.sh` (needs Postgres 16 binaries installed).
4. **Try an import:** `uv run python scripts/import_chat.py "<path to WhatsApp Chat with X.txt, folder or .zip>"`.
   Or run `make dev` and POST a file to `http://localhost:8000/imports` with header `X-WCM-User: admin`.

## B. Needed before M2 and later can be verified (files I do not have)
Add these to the repo (push to `main` or tell me where they are):
- `CLAUDE.md` (working style) and `BUILD_PROMPT.md`
- `docs/DECISIONS.md` (D1-D15) and `docs/KNOWN_INCONSISTENCIES.md` (INC-01..61)
- `docs/WhatsApp_Chaos_Management_Technical_Blueprint_v0.1.pdf`
- `fixtures/` : `whatsapp_exports/native_android/`, `whatsapp_exports/alt_tool/`, `zips/`, `direct_uploads/`, and `fixtures/expected/golden_cases.yaml`
- Real `config/*.yaml`: `groups.yaml`, `people.yaml`, `header_aliases.yaml`, `status_map.yaml`, `precedence.yaml`. The ones now in the repo are placeholders and will be overwritten.
- Fixtures contain real business data. Confirm you are happy with them being in this (private) repo, and with media being sent to the Anthropic API in M4.

## B2. New with M2
- Real `config/header_aliases.yaml`, `config/status_map.yaml` and the new `config/workbooks.yaml` (master row threshold,
  date order, column groups for timeline/dispatch). Mine are guesses and will change the diff numbers.
- Optional: `uv add xlrd` if the owner's sheets include old `.xls` files (otherwise they go to the review queue).
- To try it: import a chat export folder that contains the `.xlsx` files (`scripts/import_chat.py <folder>`),
  then in Neon run `select role, version_no, family_key, posted_at from workbook_imports order by posted_at;`
  and `select summary from workbook_diffs;`. No new migration is needed (tables already exist).

## C. Needed from M3/M4 (not yet)
- An **Anthropic API key** in `.env` as `ANTHROPIC_API_KEY=...` for live model runs (`make record`, `make eval-live`). Tests run without it using recorded responses. This is a paid service, so it needs your approval and a spend limit.
- Optionally set `WCM_VISION_MODEL`, `WCM_TEXT_MODEL`, `WCM_ASSISTANT_MODEL` (defaults are in `.env.example`).
- `ffmpeg` installed locally for video keyframes (M4).
- Node 20+ and npm for the web app (M5).

## D. Decisions I will stop and ask about
Precedence between sources (D07), what "verified" means (D08), auto-publishing new categories (D10), anything that changes DECISIONS.md, any real WhatsApp connector.

## E. Housekeeping
- Work is on branch `claude/eager-sagan-vqh6gs`. No pull request has been opened; tell me if you want one, or a different branch name.
- Dev users (`admin`, `tata_viewer`, `maruti_viewer`) are fixed names checked via the `X-WCM-User` header; they are not yet rows in the database (comes with M5).
