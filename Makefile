# Thin wrappers over the commands you would type anyway. `make help` lists them.
.DEFAULT_GOAL := help
.PHONY: help up down restart logs ps build backup backup-now shell rcon test lint fmt clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start the server and backup sidecar
	docker compose up -d

down: ## Stop everything (volumes, and your world, survive)
	docker compose down

restart: ## Restart the game server only
	docker compose restart pz-server

logs: ## Follow logs from all containers
	docker compose logs -f

ps: ## Show container status
	docker compose ps

build: ## Rebuild images
	docker compose build

backup-now: ## Take a backup immediately, outside the schedule
	docker compose run --rm pz-backup backup

shell: ## Open a shell in the game server container
	docker compose exec pz-server bash

rcon: ## Send an RCON command, e.g. make rcon CMD="players"
	docker compose run --rm pz-backup rcon $(CMD)

test: ## Run the test suite
	pytest -v

lint: ## Lint and format-check
	ruff check src tests
	ruff format --check src tests

fmt: ## Auto-format
	ruff format src tests
	ruff check --fix src tests

clean: ## Remove build artefacts and caches
	rm -rf .pytest_cache .ruff_cache **/__pycache__ *.egg-info
