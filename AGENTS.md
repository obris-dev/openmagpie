# AGENTS.md

Conventions for AI coding agents (Claude Code, Codex, Cursor, etc.) and human contributors.

This file is cross-cutting only. Each top-level app owns its own conventions:

- [apps/core/AGENTS.md](apps/core/AGENTS.md): Django backend (models, services, auth, plugins)
- [apps/cli/AGENTS.md](apps/cli/AGENTS.md): `magpie` CLI (Typer + httpx + Pydantic)
- [web/AGENTS.md](web/AGENTS.md): pnpm workspace (Next.js + shared packages)

When working in `apps/core/`, `apps/cli/`, or `web/`, load the matching `AGENTS.md` alongside this one.

## What is OpenMagpie

An open-source semantic listener. Tell it what to listen for; it picks out what matters from any stream and learns over time.

Three things stay pluggable across the codebase:
- **Connectors** (Reddit, GitHub, GDocs, Slack, ...): yield typed `SourcePayload` subclasses from each source
- **Engines** (Ollama, future Anthropic/OpenAI/keyword): BYO LLM that judges a `SourcePayload` for a semantic-filter action
- **Action kinds** (semantic_filter, webhook, log, future keyword/Slack/email): the steps a Watch runs over each feed item (filter, then deliver)

The product is **only** a listener: watches, judges, learns, notifies. It does NOT auto-reply, post back to sources, run workflows, or generate reports. Scope test: if a feature isn't listening / learning / notifying, it's out.

## Repo layout

```
apps/core/                  Django backend (see apps/core/AGENTS.md)
apps/cli/                   magpie CLI (see apps/cli/AGENTS.md)
packages/openmagpie-schema/ pure Pydantic models shared by core + cli
web/                        pnpm workspace, Next.js (see web/AGENTS.md)
make/                       Per-concern Makefile targets
scripts/                    Helper scripts (lint, whitespace, make-help)
pyproject.toml + uv.lock    uv workspace root (one lock for all members)
```

## Naming (cross-cutting domain vocabulary)

- The unit of attention is a **`Watch`**: a subscription over a set of feeds plus an ordered chain of actions. "Listener" survives as the product pitch ("a Watch is a listener"), not a code-level node name.
- A polled item is a **`FeedItem`** (persisted Django row). The in-memory typed version a connector produces is a **`SourcePayload`** (Pydantic).
- A single action executing against one feed item is a **`WatchActionRun`** (the audit row). There is no `Event` / hit model; a successful filter is just a `WatchActionRun` that advanced the chain.
- Source connectors are named for the variant: **`RedditSubRedditConnector`** (kind=`"reddit_subreddit"`). Future Reddit variants get their own connector + kind.
- Payloads from sources are named for *what happened*: **`NewRedditPostPayload`** (`PAYLOAD_KIND="new_post"`).
- An action's typed result is a per-kind model: **`SemanticFilterResult`** (`{passed, score, reason}`), `WebhookResult`, `LogResult`.

## Cross-cutting code rules

- **State-machine values get a const object + derived type from the start.** No bare string literals in match arms or status checks. Python: `class Status(Enum): ...`. TypeScript: `const PHASE = {...} as const; type Phase = typeof PHASE[keyof typeof PHASE]`.
- **No em dashes.** Use commas or periods. Applies to UI text, comments, docs.
- **Convention docs describe what to do.** No justifications, no historical context, no "we chose X because of Y." Forward-looking constraints are fine; past-decision narratives are not.
- **Branch names are `<type>/<kebab-slug>`.** Type is a Conventional-Commits prefix (`feat` | `fix` | `docs` | `refactor` | `test` | `chore` | `ci` | `perf` | `build` | `style` | `revert`); `main` is exempt. Enforced by `scripts/check-branch-name.sh` (pre-commit + CI). See [CONTRIBUTING.md](CONTRIBUTING.md) for the per-type meanings and the PR flow.
- **PR descriptions follow `What` → `How` → `Testing` → `Notes`.** `What` is the change and why; `How` is the approach, grouped by area (Backend / Web / Docs / ...) when it spans several; `Testing` states what you ran and what passed; `Notes` is optional (caveats, follow-ups, out-of-scope). Scaffolded by [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md).

## Stack

**Django + Postgres** (a `db` service in docker-compose). The app is a multi-writer pipeline (poll + trigger/drain/flush write concurrently), which SQLite's single-writer lock can't serve; Postgres' MVCC is required, not optional.

- Web: pnpm + Next.js 16 + React 19 + Tailwind v4 + zod.
- CLI: Typer + httpx + Pydantic.

Deliberately deferred until concrete need:
- **Redis / Celery / Celery-beat** when async or scheduled work shows up
- **Garage** (S3-compatible blob storage; NOT MinIO) when we need blobs
- **Django admin**: `manage.py shell` or custom commands for v0

Do NOT proactively re-add deferred infra. Wait for a concrete need.

## Tooling preferences

Prefer OSS-aligned / community-governed tools over commercial-OSS hybrids with a history of license rugs.
- **Blob storage** (when needed): Garage, not MinIO
- **Type checker**: ty (not mypy unless ty proves insufficient)

## Dev loop

```
make build              build images and start
make up / down          start / stop stack
make logs               tail everything
make dev-migrate        run migrations
make dev-makemigrations ARGS="<app> --name <descriptive_name>"
make dev-test
make dev-lint           ruff + whitespace/trailing-newline
make dev-lint-fix       auto-fix
make dev-types          ty
make dev-check          lint + types + test
make help               full list
```
