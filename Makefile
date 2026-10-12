.PHONY: up down db-local migrate test lint typecheck check dev import-fixtures record eval-live web-test

PY := uv run

up:            ## start Postgres (docker compose, else local pg fallback)
	@docker compose up -d --wait db 2>/dev/null || scripts/localpg.sh start
down:
	@docker compose down 2>/dev/null || scripts/localpg.sh stop
migrate:
	$(PY) alembic upgrade head
test:
	$(PY) pytest -q
lint:
	$(PY) ruff check app tests scripts && $(PY) ruff format --check app tests scripts
typecheck:
	$(PY) mypy app
check: lint typecheck test
dev:
	$(PY) uvicorn app.api.main:app --reload --port 8000
import-fixtures:
	$(PY) python scripts/import_fixtures.py
record:
	$(PY) python scripts/record_cassettes.py
eval-live:
	$(PY) python scripts/eval_live.py
web-test:
	cd web && npm test
