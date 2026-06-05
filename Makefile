# OnMixAI developer task runner. All Python tooling runs through the backend
# uv-managed virtualenv (backend/.venv), pinned to Python 3.12 (CLAUDE.md §10).
.DEFAULT_GOAL := help

BACKEND := backend
VENV := $(BACKEND)/.venv/bin
COMPOSE := docker compose -f infra/docker-compose.yml

.PHONY: help install dev test lint typecheck contracts migrate fmt verify

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the venv and install backend deps (dev extras)
	cd $(BACKEND) && uv venv --python 3.12 .venv && uv pip install --python .venv -e ".[dev]"

dev: ## Run the API locally with autoreload
	cd $(BACKEND) && $(abspath $(VENV))/uvicorn src.main:create_app --factory --reload

test: ## Run the full test suite with coverage gate (parser tests run isolated; see ADR 0008)
	cd $(BACKEND) && PYTEST=$(abspath $(VENV))/pytest ./scripts/run-tests.sh

lint: ## Ruff lint + format check
	cd $(BACKEND) && $(abspath $(VENV))/ruff check . && $(abspath $(VENV))/ruff format --check .

typecheck: ## mypy --strict
	cd $(BACKEND) && $(abspath $(VENV))/mypy src/

contracts: ## import-linter architecture contracts
	cd $(BACKEND) && $(abspath $(VENV))/lint-imports

migrate: ## Apply database migrations to head
	cd $(BACKEND) && $(abspath $(VENV))/alembic upgrade head

fmt: ## Auto-format the codebase
	cd $(BACKEND) && $(abspath $(VENV))/ruff format . && $(abspath $(VENV))/ruff check --fix .

verify: lint typecheck test ## Run lint + typecheck + tests (the local quality gate)
