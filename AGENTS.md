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
- **Engines** (any OpenAI-compatible `/v1` LLM: Ollama, vLLM, llama.cpp, LM Studio, OpenAI, ...; future keyword/other): BYO LLM that judges a `SourcePayload` for a semantic-filter action
- **Action kinds** (semantic_filter, extract, webhook, log, future keyword/Slack/email): the steps a Watch runs over each feed item -- three families: FILTER (gate the chain), EXTRACT (hydrate structured fields onto the run), DELIVER

The product is **only** a listener: watches, judges, learns, notifies. It does NOT auto-reply, post back to sources, run workflows, or generate new reports/analysis as a product surface. Exporting your OWN activity is in scope though -- e.g. `magpie activity export` dumping an action's runs (incl. extracted fields) to CSV is observability over data you already produced, not report generation. Scope test: if a feature isn't listening / learning / notifying (or surfacing what it already did), it's out.

## Repo layout

```
apps/core/                  Django backend (see apps/core/AGENTS.md)
apps/cli/                   magpie CLI (see apps/cli/AGENTS.md)
packages/openmagpie-schema/ pure Pydantic models shared by core + cli
web/                        pnpm workspace, Next.js (see web/AGENTS.md)
make/                       Per-concern Makefile targets
scripts/                    quickstart installer (quickstart/{bootstrap,preflight,run,seed,tick}.sh) + dev tooling (check-docker, hooks, lint/whitespace/branch checks, make-help)
pyproject.toml + uv.lock    uv workspace root (one lock for all members)
```

## Naming (cross-cutting domain vocabulary)

- The unit of attention is a **`Watch`**: a subscription over a set of feeds plus an ordered chain of actions. "Listener" survives as the product pitch ("a Watch is a listener"), not a code-level node name.
- A polled item is a **`FeedItem`** (persisted Django row). The in-memory typed version a connector produces is a **`SourcePayload`** (Pydantic).
- A single action executing against one feed item is a **`WatchActionRun`** (the audit row), surfaced publicly (CLI + REST) as **`activity`** (the model name stays `WatchActionRun`; the unit is an "activity entry"). There is no `Event` / hit model; a successful filter is just a `WatchActionRun` that advanced the chain.
- **Resource names qualify by parent only when dependent.** A first-class entity, a hub other resources are addressed relative to, is named bare (`Watch`, `Feed`, `WatchAction`). A dependent record or component, meaningless apart from its parent, is qualified by it (a feed's items / sources, an action's activity / deliveries). This drives both REST route names ([apps/core/AGENTS.md](apps/core/AGENTS.md)) and CLI command nouns ([apps/cli/AGENTS.md](apps/cli/AGENTS.md)).
- Source connectors are named for the variant: **`RedditSubRedditConnector`** (kind=`"reddit_subreddit"`). Future Reddit variants get their own connector + kind.
- Payloads from sources are named for *what happened*: **`NewRedditPostPayload`** (`PAYLOAD_KIND="new_post"`).
- An action's typed result is a per-kind model: **`SemanticFilterResult`** (`{passed, score, reason}`), `WebhookResult`, `LogResult`.

## Cross-cutting code rules

- **State-machine values get a const object + derived type from the start.** No bare string literals in match arms or status checks. Python: `class Status(Enum): ...`. TypeScript: `const PHASE = {...} as const; type Phase = typeof PHASE[keyof typeof PHASE]`.
- **No em dashes, and no `--` as a stand-in for one.** Rewrite: commas, parens, or two sentences. Applies to UI text, comments, docs.
- **Shell scripts are POSIX `sh`.** `#!/bin/sh`, no bashisms (`[[ ]]`, `=~`, arrays, `local`, `set -o pipefail`, `${BASH_SOURCE}`, `< <(...)`). Everything in `scripts/` must pass `shellcheck -s sh` (enforced in pre-commit + CI).
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
make local-migrate        run migrations
make local-makemigrations ARGS="<app> --name <descriptive_name>"
make local-test
make local-lint           ruff + whitespace/trailing-newline
make local-lint-fix       auto-fix
make local-types          ty
make local-check          lint + types + test
make help               full list
```

## Releases & changelog

release-please owns versioning + the changelogs, generated from the
conventional-commit history, in TWO tracks: the product (`CHANGELOG.md` +
`version.txt`, tag `v<x.y.z>`) and the CLI (`apps/cli/CHANGELOG.md`, tag
`cli-v<x.y.z>` = the PyPI `openmagpie`). Each entry comes from a merged commit's
SUBJECT line.

- **A changelog entry says what the feature DOES for a user, not how it's built.**
  Lead with the capability ("a watch action that pulls fields you declare out of
  each item"); leave out internal types, migration notes, and engine internals
  (that's implementation). Frame the product track for a server/product reader, the
  CLI track for a CLI user.
- **For notes richer than the subject, write the commit / PR BODY.** release-please
  appends it under the entry, so the body is the DURABLE home for release notes.
- **release-please OWNS the release branches** (`release-please--branches--main*`):
  it regenerates and force-pushes the changelog on every merge to main. A hand edit
  to a release PR's `CHANGELOG.md` is clobbered if release-please re-runs before that
  PR merges, so only touch one up right before merging (else use the commit body).
- Prose follows the no-em-dash rule above.
