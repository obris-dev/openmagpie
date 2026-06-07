.PHONY: quickstart install-cli up build down logs logs-core logs-web dev-exec dev-manage dev-test dev-makemigrations dev-dbshell dev-migrate dev-bootstrap dev-tick up-jobs down-jobs _job-up dev-lint dev-lint-fix dev-types dev-check dev-web dev-web-shell dev-cli-sync dev-cli hooks

quickstart: ## One command from a fresh clone: env + build (wait healthy) + migrate
	@test -f apps/core/.env || { cp apps/core/.env.example apps/core/.env; echo "Created apps/core/.env from .env.example"; }
	docker compose up --build -d --wait
	$(MAKE) dev-migrate
	@echo ""
	@echo "Ready. Next:"
	@echo "  App:  http://localhost:3001  (create an account; you're signed in)"
	@echo "  Site: http://localhost:3000  (marketing landing)"
	@echo "  CLI:  make install-cli   then   magpie auth login"
	@echo ""
	@echo "Heads up: OpenMagpie is BYO-LLM. Point OLLAMA_URL in apps/core/.env at"
	@echo "an Ollama you control (default host.docker.internal:11434)."

install-cli: ## Install the DEV magpie CLI on your PATH (snapshot of this checkout)
	# NOT --editable: the cli depends on the openmagpie-schema workspace package,
	# and an editable tool install lets a stale schema copy shadow the live one
	# (ModuleNotFoundError on newer submodules). Non-editable builds a CURRENT
	# schema wheel from the workspace. It's a SNAPSHOT, not live: re-run after a
	# git pull to update. --reinstall makes re-running idempotent.
	uv tool install --reinstall ./apps/cli
	@echo "Installed the dev CLI (snapshot of this checkout; re-run after a pull). Try: magpie auth login"

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

logs-web: ## Tail Next.js web logs (app + marketing)
	docker compose logs -f web

dev-exec: ## Run a command in a container (e.g. make dev-exec SVC=core CMD="uv run ruff check .")
	docker compose exec $(SVC) $(CMD)

dev-manage: ## Run Django manage.py command (e.g. make dev-manage CMD=shell)
	$(MAKE) dev-exec SVC=core CMD="uv run --package openmagpie-core python apps/core/manage.py $(CMD)"

dev-test: ## Run Django test suite
	$(MAKE) dev-manage CMD=test

dev-makemigrations: ## Generate Django migration files (e.g. make dev-makemigrations ARGS="myapp")
	$(MAKE) dev-manage CMD="makemigrations $(ARGS)"

dev-dbshell: ## Open a psql shell on the Postgres db service
	docker compose exec db psql -U openmagpie -d openmagpie

dev-migrate: ## Run Django database migrations + ensure cache table exists + bootstrap OAuth Application
	$(MAKE) dev-manage CMD=migrate
	$(MAKE) dev-manage CMD=createcachetable
	$(MAKE) dev-manage CMD=bootstrap_oauth_app

dev-bootstrap: ## Alias for dev-migrate (first-run setup)
	$(MAKE) dev-migrate

dev-tick: ## Run one pipeline pass: poll feeds -> trigger watches -> drain runs -> flush digests
	$(MAKE) dev-manage CMD="poll_due_feeds"
	$(MAKE) dev-manage CMD="process_due_watches"
	$(MAKE) dev-manage CMD="process_due_runs"
	$(MAKE) dev-manage CMD="process_due_digests"

# Background tickers: each stage on its OWN cadence (they're decoupled ;
# poll writes items, trigger enqueues runs, drain executes them). Each
# command is a SingleFlightCommand, so a pass that outlasts its interval
# just self-skips the next tick; loops never stack. pid+log per stage live
# under .jobs/ (gitignored). Override any cadence: make up-jobs DRAIN_INTERVAL=30
# (Prod scheduling = plain cron per command on these cadences; no flock /
# singleton infra needed, since the command self-skips overlaps.)
JOBS_DIR := .jobs
POLL_INTERVAL ?= 300
TRIGGER_INTERVAL ?= 300
DRAIN_INTERVAL ?= 60
DIGEST_INTERVAL ?= 60

up-jobs: ## Start poll/trigger/drain/digest as independent background tickers
	@mkdir -p $(JOBS_DIR)
	@$(MAKE) --no-print-directory _job-up NAME=poll    CMD=poll_due_feeds      INTERVAL=$(POLL_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=trigger CMD=process_due_watches INTERVAL=$(TRIGGER_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=drain   CMD=process_due_runs    INTERVAL=$(DRAIN_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=digest  CMD=process_due_digests INTERVAL=$(DIGEST_INTERVAL)

_job-up:
	@if [ -f $(JOBS_DIR)/$(NAME).pid ] && kill -0 $$(cat $(JOBS_DIR)/$(NAME).pid) 2>/dev/null; then \
		echo "$(NAME) already running (pid $$(cat $(JOBS_DIR)/$(NAME).pid))"; \
	else \
		nohup sh -c 'while true; do $(MAKE) dev-manage CMD=$(CMD); sleep $(INTERVAL); done' \
			>> $(JOBS_DIR)/$(NAME).log 2>&1 & echo $$! > $(JOBS_DIR)/$(NAME).pid; \
		echo "$(NAME) started (pid $$(cat $(JOBS_DIR)/$(NAME).pid)) every $(INTERVAL)s -> $(JOBS_DIR)/$(NAME).log"; \
	fi

down-jobs: ## Stop the background tickers started by up-jobs
	@for n in poll trigger drain digest; do \
		if [ -f $(JOBS_DIR)/$$n.pid ]; then \
			kill $$(cat $(JOBS_DIR)/$$n.pid) 2>/dev/null; rm -f $(JOBS_DIR)/$$n.pid; echo "$$n stopped"; \
		else echo "$$n not running"; fi; \
	done

dev-web: ## Start (or restart) the Next.js dev container (app + marketing) and tail its logs
	docker compose up -d web
	docker compose logs -f web

dev-web-shell: ## Open a shell in the web container
	docker compose exec web sh

dev-cli-sync: ## Sync the uv workspace (one root .venv for all members)
	uv sync
	@echo "Run: make dev-cli ARGS=\"auth login\""

dev-cli: ## Run the magpie CLI via uv (e.g. make dev-cli ARGS="auth login")
	uv run --package openmagpie-cli magpie $(ARGS)

dev-lint: ## Run linters (ruff + whitespace/final-newline on tracked text files)
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format --check ."
	./scripts/check-whitespace.sh

dev-lint-fix: ## Auto-fix lint issues
	$(MAKE) dev-exec SVC=core CMD="uv run ruff check --fix ."
	$(MAKE) dev-exec SVC=core CMD="uv run ruff format ."
	./scripts/check-whitespace.sh --fix

dev-types: ## Run ty static type checker (core + shared schema pkg)
	$(MAKE) dev-exec SVC=core CMD="uv run --package openmagpie-core ty check apps/core packages/openmagpie-schema"

dev-check: ## Run lint + types + tests (pre-commit habit)
	$(MAKE) dev-lint
	$(MAKE) dev-types
	$(MAKE) dev-test

hooks: ## Install git pre-commit hooks (.pre-commit-config.yaml)
	uvx pre-commit install
