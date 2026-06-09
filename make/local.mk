.PHONY: quickstart install-cli up build down logs logs-core logs-web local-exec local-manage local-test local-makemigrations local-dbshell local-migrate local-bootstrap local-tick up-jobs down-jobs _job-up local-lint local-lint-fix local-types local-check local-web local-web-reinstall local-web-shell local-cli-sync local-cli hooks

quickstart: ## One command from a fresh clone: env + build (wait healthy) + migrate
	@test -f apps/core/.env || { cp apps/core/.env.example apps/core/.env; echo "Created apps/core/.env from .env.example"; }
	docker compose up --build -d --wait
	$(MAKE) local-migrate
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

local-exec: ## Run a command in a container (e.g. make local-exec SVC=core CMD="uv run ruff check .")
	docker compose exec $(SVC) $(CMD)

local-manage: ## Run Django manage.py command (e.g. make local-manage CMD=shell)
	$(MAKE) local-exec SVC=core CMD="uv run --package openmagpie-core python apps/core/manage.py $(CMD)"

local-test: ## Run Django test suite
	$(MAKE) local-manage CMD=test

local-makemigrations: ## Generate Django migration files (e.g. make local-makemigrations ARGS="myapp")
	$(MAKE) local-manage CMD="makemigrations $(ARGS)"

local-dbshell: ## Open a psql shell on the Postgres db service
	docker compose exec db psql -U openmagpie -d openmagpie

local-migrate: ## Run Django database migrations + ensure cache table exists + bootstrap OAuth Application
	$(MAKE) local-manage CMD=migrate
	$(MAKE) local-manage CMD=createcachetable
	$(MAKE) local-manage CMD=bootstrap_oauth_app

local-bootstrap: ## Alias for local-migrate (first-run setup)
	$(MAKE) local-migrate

local-tick: ## Run one pipeline pass: poll feeds -> trigger watches -> drain runs -> flush digests -> send email
	$(MAKE) local-manage CMD="poll_due_feeds"
	$(MAKE) local-manage CMD="process_due_watches"
	$(MAKE) local-manage CMD="process_due_runs"
	$(MAKE) local-manage CMD="process_due_digests"
	$(MAKE) local-manage CMD="send_outbound_emails"

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
EMAIL_INTERVAL ?= 60

up-jobs: ## Start poll/trigger/drain/digest/email as independent background tickers
	@mkdir -p $(JOBS_DIR)
	@$(MAKE) --no-print-directory _job-up NAME=poll    CMD=poll_due_feeds      INTERVAL=$(POLL_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=trigger CMD=process_due_watches INTERVAL=$(TRIGGER_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=drain   CMD=process_due_runs    INTERVAL=$(DRAIN_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=digest  CMD=process_due_digests INTERVAL=$(DIGEST_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=email   CMD=send_outbound_emails INTERVAL=$(EMAIL_INTERVAL)

_job-up:
	@if [ -f $(JOBS_DIR)/$(NAME).pid ] && kill -0 $$(cat $(JOBS_DIR)/$(NAME).pid) 2>/dev/null; then \
		echo "$(NAME) already running (pid $$(cat $(JOBS_DIR)/$(NAME).pid))"; \
	else \
		nohup sh -c 'while true; do $(MAKE) local-manage CMD=$(CMD); sleep $(INTERVAL); done' \
			>> $(JOBS_DIR)/$(NAME).log 2>&1 & echo $$! > $(JOBS_DIR)/$(NAME).pid; \
		echo "$(NAME) started (pid $$(cat $(JOBS_DIR)/$(NAME).pid)) every $(INTERVAL)s -> $(JOBS_DIR)/$(NAME).log"; \
	fi

down-jobs: ## Stop the background tickers started by up-jobs
	@for n in poll trigger drain digest email; do \
		if [ -f $(JOBS_DIR)/$$n.pid ]; then \
			kill $$(cat $(JOBS_DIR)/$$n.pid) 2>/dev/null; rm -f $(JOBS_DIR)/$$n.pid; echo "$$n stopped"; \
		else echo "$$n not running"; fi; \
	done

local-web: ## Start (or restart) the Next.js dev container (app + marketing) and tail its logs
	docker compose up -d web
	docker compose logs -f web

local-web-reinstall: ## Recreate the web container so it re-runs pnpm install (after adding a web dependency), then tail logs
	docker compose up -d --force-recreate web
	docker compose logs -f web

local-web-shell: ## Open a shell in the web container
	docker compose exec web sh

local-cli-sync: ## Sync the uv workspace (one root .venv for all members)
	uv sync
	@echo "Run: make local-cli ARGS=\"auth login\""

local-cli: ## Run the magpie CLI via uv (e.g. make local-cli ARGS="auth login")
	uv run --package openmagpie-cli magpie $(ARGS)

local-lint: ## Run linters (ruff + whitespace/final-newline on tracked text files)
	$(MAKE) local-exec SVC=core CMD="uv run ruff check ."
	$(MAKE) local-exec SVC=core CMD="uv run ruff format --check ."
	./scripts/check-whitespace.sh

local-lint-fix: ## Auto-fix lint issues
	$(MAKE) local-exec SVC=core CMD="uv run ruff check --fix ."
	$(MAKE) local-exec SVC=core CMD="uv run ruff format ."
	./scripts/check-whitespace.sh --fix

local-types: ## Run ty static type checker (core + shared schema pkg)
	$(MAKE) local-exec SVC=core CMD="uv run --package openmagpie-core ty check apps/core packages/openmagpie-schema"

local-check: ## Run lint + types + tests (pre-commit habit)
	$(MAKE) local-lint
	$(MAKE) local-types
	$(MAKE) local-test

hooks: ## Install git pre-commit hooks (.pre-commit-config.yaml)
	uvx pre-commit install
