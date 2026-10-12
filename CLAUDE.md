# Working style (written in M0 because the owner's CLAUDE.md was not supplied)

- Build milestones M0..M7 in order. Each ends with `make check` green, a commit, and a `docs/PROGRESS.md` entry.
- Layering is strict: source -> observation (append-only) -> derived state. Never update observations in place.
- Rules first, model second. Model output is a proposal; `validate` enforces invariants.
- Uncertain means review queue, not a guess. Zero wrong auto-links.
- No PII in logs (use `app.core.logging`). Config comes from `config/*.yaml`, never hard-coded.
- Stop and ask the owner for: changes to DECISIONS.md, precedence (D07), "verified" (D08), auto-publishing categories (D10), new paid services, real WhatsApp connectors.
- Commands: `make up migrate test lint typecheck check dev import-fixtures record eval-live`.
- Only this repo (`whatsapp_chaos_management`) is to be modified.
