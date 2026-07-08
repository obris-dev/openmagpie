.PHONY: install-local-cli upgrade up build down restart restart-web logs logs-core logs-web local-exec local-manage local-test local-makemigrations local-dbshell local-migrate local-tick up-jobs down-jobs _job-up local-lint local-lint-fix local-types local-check local-web local-web-reinstall local-web-shell local-cli-sync local-cli hooks

# Getting started is the curl|sh installer (scripts/quickstart/bootstrap.sh) or,
# in a clone, ./scripts/quickstart/run.sh. That orchestration lives in POSIX sh,
# make-free, so make stays the dev-loop interface.
#
# These targets call scripts via `./scripts/...` (make runs in a git checkout
# where the 100755 bit is intact). The quickstart scripts instead invoke each
# other via `sh ./script`, which doesn't depend on +x surviving a fresh clone
# or a tarball download.

install-local-cli: ## Install the local magpie CLI on your PATH (snapshot of this checkout)
	# Logic lives in scripts/install-local-cli.sh (POSIX sh) so the quickstart
	# can install the CLI without make ; this target is the dev-loop alias. The
	# script needs uv (and prints how to get it if missing).
	@./scripts/install-local-cli.sh --force

upgrade: ## Upgrade this install to the latest release (advance tag, rebuild, migrate, refresh CLI); data preserved
	@sh ./scripts/upgrade.sh

up: ## Start local Docker dev environment
	docker compose up -d

build: ## Rebuild Docker images and start
	@./scripts/check-docker.sh
	docker compose up --build -d

down: ## Stop local Docker dev environment
	docker compose down

restart: ## Restart all services in place (bounces the dev servers; recompiles .next). Use `make down && make up` after docker-compose.yml changes.
	docker compose restart

restart-web: ## Restart just the web container (app + marketing + blog); use when host-side edits have confused its dev server
	docker compose restart web

logs: ## Tail all Docker container logs
	docker compose logs -f

logs-core: ## Tail Django logs
	docker compose logs -f core

logs-web: ## Tail Next.js web logs (app + marketing + blog)
	docker compose logs -f web

local-exec: ## Run a command in a container (e.g. make local-exec SVC=core CMD="uv run ruff check .")
	docker compose exec $(SVC) $(CMD)

local-manage: ## Run Django manage.py command (e.g. make local-manage CMD=shell)
	$(MAKE) local-exec SVC=core CMD="uv run --package openmagpie-core python apps/core/manage.py $(CMD)"

local-test: ## Run Django test suite (auto-discovers every app)
	# Run from apps/core: the apps are importable as top-level there, so Django's
	# test discovery finds them all. From the repo-root cwd it finds zero.
	docker compose exec core sh -c 'cd apps/core && uv run --package openmagpie-core python manage.py test --noinput'

local-makemigrations: ## Generate Django migration files (e.g. make local-makemigrations ARGS="myapp")
	$(MAKE) local-manage CMD="makemigrations $(ARGS)"

local-dbshell: ## Open a psql shell on the Postgres db service
	docker compose exec db psql -U openmagpie -d openmagpie

local-migrate: ## Run Django database migrations + ensure cache table exists + bootstrap OAuth Application
	# The full manual DB setup / re-run (e.g. after `makemigrations`). The
	# quickstart (scripts/quickstart/run.sh) runs migrate + bootstrap once, and
	# the core-setup compose one-shot runs createcachetable before core serves
	# (for /healthz); keep these three in sync with those.
	$(MAKE) local-manage CMD=migrate
	$(MAKE) local-manage CMD=createcachetable
	$(MAKE) local-manage CMD=bootstrap_oauth_app

local-tick: ## Run one pipeline pass: poll feeds -> trigger watches -> run backfills -> drain runs -> flush digests -> send email
	$(MAKE) local-manage CMD="poll_due_feeds"
	$(MAKE) local-manage CMD="process_due_watches"
	$(MAKE) local-manage CMD="process_due_backfills"
	$(MAKE) local-manage CMD="process_due_runs"
	$(MAKE) local-manage CMD="process_due_digests"
	$(MAKE) local-manage CMD="send_outbound_emails"
	$(MAKE) local-manage CMD="emit_telemetry_heartbeat"

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
BACKFILL_INTERVAL ?= 60
DRAIN_INTERVAL ?= 60
DIGEST_INTERVAL ?= 60
EMAIL_INTERVAL ?= 60
# The heartbeat command self-throttles to ~once/day; this is just how often it's
# given a chance to fire, so a coarse hourly poke is plenty.
HEARTBEAT_INTERVAL ?= 3600

up-jobs: ## Start poll/trigger/backfill/drain/digest/email as independent background tickers
	@mkdir -p $(JOBS_DIR)
	@# Pre-flight: a lock held BEFORE we start anything is suspicious (a prior
	@# run's orphan or another machine), and the new tickers would skip every
	@# pass until it clears. Warn, don't block (|| true), but DON'T hide stderr:
	@# a failed check (container down, cache unreachable) should be visible.
	@$(MAKE) --no-print-directory local-manage CMD="clear_job_locks --all --dry-run" || true
	@$(MAKE) --no-print-directory _job-up NAME=poll    CMD=poll_due_feeds      INTERVAL=$(POLL_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=trigger CMD=process_due_watches INTERVAL=$(TRIGGER_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=backfill CMD=process_due_backfills INTERVAL=$(BACKFILL_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=drain   CMD=process_due_runs    INTERVAL=$(DRAIN_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=digest  CMD=process_due_digests INTERVAL=$(DIGEST_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=email   CMD=send_outbound_emails INTERVAL=$(EMAIL_INTERVAL)
	@$(MAKE) --no-print-directory _job-up NAME=heartbeat CMD=emit_telemetry_heartbeat INTERVAL=$(HEARTBEAT_INTERVAL)

_job-up:
	@# Print the command name (CMD) alongside the ticker name so the start
	@# output maps to the job-lock names from `clear_job_locks` (e.g. the
	@# `poll` ticker runs `poll_due_feeds`, whose lock is `feeds.poll_due_feeds`).
	@if [ -f $(JOBS_DIR)/$(NAME).pid ] && kill -0 $$(cat $(JOBS_DIR)/$(NAME).pid) 2>/dev/null; then \
		echo "$(NAME) ($(CMD)) already running (pid $$(cat $(JOBS_DIR)/$(NAME).pid))"; \
	else \
		nohup sh -c 'while true; do $(MAKE) local-manage CMD=$(CMD); sleep $(INTERVAL); done' \
			>> $(JOBS_DIR)/$(NAME).log 2>&1 & echo $$! > $(JOBS_DIR)/$(NAME).pid; \
		echo "$(NAME) ($(CMD)) started (pid $$(cat $(JOBS_DIR)/$(NAME).pid)) every $(INTERVAL)s -> $(JOBS_DIR)/$(NAME).log"; \
	fi

down-jobs: ## Stop the background tickers started by up-jobs (and clear their job locks)
	@for n in poll trigger backfill drain digest email heartbeat; do \
		if [ -f $(JOBS_DIR)/$$n.pid ]; then \
			kill $$(cat $(JOBS_DIR)/$$n.pid) 2>/dev/null; rm -f $(JOBS_DIR)/$$n.pid; echo "$$n stopped"; \
		else echo "$$n not running"; fi; \
	done
	@echo "best-effort clearing job locks (an in-container pass may still be finishing; jobs are single-flight over idempotent work, so a brief overlap is harmless)..."
	@$(MAKE) --no-print-directory local-manage CMD="clear_job_locks --all" || true

local-web: ## Start (or restart) the Next.js dev container (app + marketing + blog) and tail its logs
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
	uv run --package openmagpie magpie $(ARGS)

local-lint: ## Run linters (ruff + whitespace/final-newline on tracked text files)
	$(MAKE) local-exec SVC=core CMD="uv run ruff check ."
	$(MAKE) local-exec SVC=core CMD="uv run ruff format --check ."
	./scripts/check-whitespace.sh

local-lint-fix: ## Auto-fix lint issues
	$(MAKE) local-exec SVC=core CMD="uv run ruff check --fix ."
	$(MAKE) local-exec SVC=core CMD="uv run ruff format ."
	./scripts/check-whitespace.sh --fix

local-types: ## Run ty static type checker (core + shared schema pkg + schema_sync tool)
	$(MAKE) local-exec SVC=core CMD="uv run --package openmagpie-core ty check apps/core packages/openmagpie-schema tools/schema_sync"

local-schema: ## Regenerate the shared JSON Schema (packages/openmagpie-schema/schema.json) from the models
	$(MAKE) local-exec SVC=core CMD="uv run --package openmagpie-core python -m tools.schema_sync.generate"

local-check: ## Run lint + types + tests (pre-commit habit)
	$(MAKE) local-lint
	$(MAKE) local-types
	$(MAKE) local-test

# Hidden (no ## so it stays out of `make help`): the quickstart installs hooks,
# this is the fallback if it was skipped (e.g. uv wasn't present then).
hooks:
	@./scripts/hooks.sh
