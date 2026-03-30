# LLMProxy — Common development tasks
# Usage: make <target>

.PHONY: setup run test bench lint typecheck docker-up docker-build docs clean help

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────

setup: ## Install deps, create .env from template
	python -m venv venv || true
	. venv/bin/activate && pip install -r requirements.txt
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "\n\033[33m>>> Edit .env with your API keys before starting\033[0m"; \
	fi
	@echo "\033[32m>>> Setup complete. Run: make run\033[0m"

# ── Run ────────────────────────────────────────────────────────

run: ## Start the proxy (local)
	@if [ ! -f .env ]; then echo "\033[31mERROR: .env not found. Run: make setup\033[0m"; exit 1; fi
	. venv/bin/activate && python main.py

run-minimal: ## Start with minimal config (single provider)
	@if [ ! -f .env ]; then echo "\033[31mERROR: .env not found. Run: make setup\033[0m"; exit 1; fi
	. venv/bin/activate && CONFIG_FILE=config.minimal.yaml python main.py

# ── Testing ────────────────────────────────────────────────────

test: ## Run test suite
	. venv/bin/activate && python -m pytest tests/ \
		--ignore=tests/test_e2e.py \
		--ignore=tests/integrated_test.py \
		--ignore=tests/test_store.py \
		--ignore=tests/test_openapi_contracts.py \
		--ignore=tests/test_firewall_fuzz.py \
		--ignore=tests/test_pii_hypothesis.py \
		--ignore=tests/test_benchmarks.py \
		-q --tb=short

test-all: ## Run all tests including optional deps
	. venv/bin/activate && python -m pytest tests/ -q --tb=short

bench: ## Run performance benchmarks
	. venv/bin/activate && python -m pytest tests/test_benchmarks.py \
		--benchmark-only --benchmark-disable-gc -v

# ── Code Quality ───────────────────────────────────────────────

lint: ## Run linter (ruff)
	. venv/bin/activate && ruff check . --fix

typecheck: ## Run type checker (mypy)
	. venv/bin/activate && mypy core/ proxy/ --ignore-missing-imports

syntax: ## Verify all Python files parse
	python -m compileall -q core/ proxy/ plugins/ store/ main.py models.py

# ── Docker ─────────────────────────────────────────────────────

docker-build: ## Build Docker image
	docker build -t llmproxy .

docker-up: ## Start with Docker Compose
	@if [ ! -f .env ]; then cp .env.example .env; echo "\033[33m>>> Edit .env with your API keys\033[0m"; fi
	docker compose up -d
	@echo "\033[32m>>> LLMProxy running at http://localhost:8090\033[0m"
	@echo "    Health: curl http://localhost:8090/health"
	@echo "    Logs:   docker compose logs -f llmproxy"

docker-down: ## Stop Docker Compose
	docker compose down

docker-logs: ## Tail Docker logs
	docker compose logs -f llmproxy

# ── Documentation ──────────────────────────────────────────────

docs: ## Serve documentation locally
	cd docs && npm install && npm run docs:dev

# ── Utilities ──────────────────────────────────────────────────

health: ## Check proxy health
	@curl -sf http://localhost:8090/health | python -m json.tool || echo "\033[31mProxy not running\033[0m"

verify-deps: ## Run supply chain verification
	. venv/bin/activate && python scripts/verify_deps.py

clean: ## Remove caches and temp files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache
	@echo "\033[32m>>> Cleaned\033[0m"
