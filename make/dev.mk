.PHONY: up build down logs logs-core logs-web dev-exec dev-manage dev-test dev-makemigrations dev-dbshell dev-migrate dev-bootstrap dev-lint dev-lint-fix dev-types dev-check dev-web dev-web-shell dev-cli-sync dev-cli

up: ## Start local Docker dev environment
	docker compose up -d

build: ## Rebuild Docker images and start
	docker compose up --build -d

down: ## Stop local Docker dev environment
	docker compose down

logs: ## Tail all Docker container logs
	docker compose logs -f

logs-core: ## Tail Django logs
	docker compose logs -f core

logs-web: ## Tail Next.js web logs
	docker compose logs -f web

dev-exec: ## Run a command in a container (e.g. make dev-exec SVC=core CMD="uv run ruff check .")
	docker compose exec $(SVC) $(CMD)

dev-manage: ## Run Django manage.py command (e.g. make dev-manage CMD=shell)
	$(MAKE) dev-exec SVC=core CMD="uv run python manage.py $(CMD)"

dev-test: ## Run Django test suite
	$(MAKE) dev-manage CMD=test

dev-makemigrations: ## Generate Django migration files (e.g. make dev-makemigrations ARGS="myapp")
	$(MAKE) dev-manage CMD="makemigrations $(ARGS)"

dev-dbshell: ## Open Django's dbshell (SQLite)
	$(MAKE) dev-manage CMD=dbshell

dev-migrate: ## Run Django database migrations + ensure cache table exists + bootstrap OAuth Application
	$(MAKE) dev-manage CMD=migrate
	$(MAKE) dev-manage CMD=createcachetable
	$(MAKE) dev-manage CMD=bootstrap_oauth_app

dev-bootstrap: ## Alias for dev-migrate (first-run setup)
	$(MAKE) dev-migrate

dev-web: ## Start (or restart) just the Next.js dev container and tail its logs
	docker compose up -d web
	docker compose logs -f web

dev-web-shell: ## Open a shell in the web container
	docker compose exec web sh

dev-cli-sync: ## Sync the magpie CLI's uv-managed env (cli/.venv)
	cd cli && uv sync
	@echo "Run: cd cli && uv run magpie auth login"

dev-cli: ## Run the magpie CLI via uv (e.g. make dev-cli ARGS="auth login")
	cd cli && uv run magpie $(ARGS)

dev-wire-schema: ## Refresh the committed wire-schema artifact from the server models
	docker compose exec -T core uv run python manage.py dump_wire_schema > cli/schema/wire_schema.json
	@echo "wrote cli/schema/wire_schema.json (commit it; CI guards staleness)"

dev-cli-types: ## Regenerate the CLI's typed models from the committed wire schema
	cd cli && uv run python scripts/gen_types.py

dev-lint: ## Run linters (ruff + whitespace/final-newline on tracked text files)
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format --check ."
	./scripts/check-whitespace.sh

dev-lint-fix: ## Auto-fix lint issues
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check --fix ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format ."
	./scripts/check-whitespace.sh --fix

dev-types: ## Run ty static type checker
	$(MAKE) dev-exec SVC=core CMD="uv run ty check ."

dev-check: ## Run lint + types + tests (pre-commit habit)
	$(MAKE) dev-lint
	$(MAKE) dev-types
	$(MAKE) dev-test
