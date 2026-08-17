.PHONY: help install dev dev-api dev-web test lint fmt typecheck build up down logs smoke clean

VENV := backend/.venv
PY := $(VENV)/bin/python

help:
	@grep -E '^[a-zA-Z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## cree le venv backend et installe le frontend
	cd backend && uv venv --python 3.12 .venv
	cd backend && uv pip install --python .venv/bin/python -e ".[dev]"
	cd frontend && npm install

dev: ## lance API + frontend (deux terminaux recommandes: dev-api / dev-web)
	@echo "make dev-api  puis  make dev-web"

dev-api: ## API FastAPI en rechargement automatique sur :8300
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8300

# NE JAMAIS arreter cette API par motif de process. Tous les produits SuiteForge
# lancent "uvicorn app.main:app": un `pkill -f "uvicorn app.main:app"` tue aussi
# ScanGithub (:8894) et les autres, et cette erreur a deja coute des balayages
# en cours a une autre session. Le port, lui, n'appartient qu'a nous.
stop-api: ## arrete UNIQUEMENT l'API SOSForge, par son port
	@lsof -ti tcp:8300 | xargs kill 2>/dev/null || echo "aucune API SOSForge sur :8300"

restart-api: stop-api ## redemarre proprement l'API SOSForge
	@sleep 1
	cd backend && SOS_DATA_DIR=./data .venv/bin/python -m uvicorn app.main:app \
		--host 127.0.0.1 --port 8300

dev-web: ## frontend Vite sur :5273 (proxy /api et /ws vers :8300)
	cd frontend && npm run dev

test: ## tests backend + frontend
	cd backend && .venv/bin/python -m pytest tests -q
	cd frontend && npx vitest run

test-backend: ## tests des normalizers sur payloads reels
	cd backend && .venv/bin/python -m pytest tests -q

test-frontend: ## tests du store, des filtres, de l'i18n et du rendu
	cd frontend && npx vitest run

lint: ## ruff
	cd backend && .venv/bin/ruff check app tests

fmt: ## formatage ruff
	cd backend && .venv/bin/ruff format app tests

typecheck: ## typescript
	cd frontend && npx tsc --noEmit

build: ## build de production du frontend
	cd frontend && npm run build

up: ## stack docker complete sur http://localhost:8380
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

smoke: ## verifie que l'API tourne et que les sources repondent
	@curl -sf http://127.0.0.1:8300/healthz | python3 -m json.tool
	@curl -sf http://127.0.0.1:8300/api/sources | python3 -c "import json,sys;[print(f\"{s['name']:9} up={s['connected']} vus={s['events_seen']}\") for s in json.load(sys.stdin)['sources']]"

clean:
	rm -rf backend/data backend/.venv frontend/node_modules frontend/dist
