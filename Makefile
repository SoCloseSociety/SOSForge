.PHONY: help install dev dev-api dev-web test lint fmt typecheck build up down logs smoke clean

VENV := backend/.venv
PY := $(VENV)/bin/python

help:
	@grep -E '^[a-zA-Z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## create the backend venv and install the frontend
	cd backend && uv venv --python 3.12 .venv
	cd backend && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm install

dev: ## run API + frontend (two terminals: dev-api / dev-web)
	@echo "make dev-api  puis  make dev-web"

dev-api: ## FastAPI with auto-reload on :8300
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8300

# NEVER stop this API by process pattern. Every SuiteForge product runs
# "uvicorn app.main:app": a `pkill -f "uvicorn app.main:app"` also kills
# ScanGithub (:8894) and the others, and that mistake has already cost another
# session its in-flight sweeps. The port, on the other hand, is ours alone.
stop-api: ## stop ONLY the SOSForge API, by its port
	@lsof -ti tcp:8300 | xargs kill 2>/dev/null || echo "no SOSForge API on :8300"

restart-api: stop-api ## restart the SOSForge API cleanly
	@sleep 1
	cd backend && SOS_DATA_DIR=./data .venv/bin/python -m uvicorn app.main:app \
		--host 127.0.0.1 --port 8300

dev-web: ## Vite frontend on :5273 (proxies /api and /ws to :8300)
	cd frontend && npm run dev

test: ## backend + frontend tests
	cd backend && .venv/bin/python -m pytest tests -q
	cd frontend && npx vitest run

test-backend: ## normalizer tests on real payloads
	cd backend && .venv/bin/python -m pytest tests -q

test-frontend: ## store, filters, i18n and rendering tests
	cd frontend && npx vitest run

lint: ## ruff
	cd backend && .venv/bin/ruff check app tests

fmt: ## ruff formatting
	cd backend && .venv/bin/ruff format app tests

typecheck: ## types: TypeScript AND Python
	cd frontend && npx tsc --noEmit
	cd backend && .venv/bin/python -m mypy app

build: ## production build of the frontend
	cd frontend && npm run build

up: ## full docker stack on http://localhost:8380
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

smoke: ## check the API is up and the sources respond
	@curl -sf http://127.0.0.1:8300/healthz | python3 -m json.tool
	@curl -sf http://127.0.0.1:8300/api/sources | python3 -c "import json,sys;[print(f\"{s['name']:9} up={s['connected']} vus={s['events_seen']}\") for s in json.load(sys.stdin)['sources']]"

clean:
	rm -rf backend/data backend/.venv frontend/node_modules frontend/dist
