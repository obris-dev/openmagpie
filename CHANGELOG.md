# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-06-09

### Added

- Personal access tokens for headless / no-browser CLI login. Mint one on the
  server with the `issue_cli_token` management command (on the local stack:
  `make local-manage CMD="issue_cli_token --email <e> --name <n>"`), then sign in
  with `magpie auth login --token` (reads the token from piped stdin or a hidden
  prompt, never argv; persisted to `~/.magpie` at `0600`). For CI, set
  `MAGPIE_TOKEN=mgp_...` in the environment instead: it's read as an ambient credential
  on every request (precedence over a stored login, never persisted), the `GH_TOKEN`
  pattern, no login step. Tokens are hashed at rest, named,
  and individually revocable via `magpie auth token list` / `create` / `revoke` or
  `DELETE /v1/auth/cli-tokens/<id>`; a token can't mint another token (browser
  login required to create). This unblocks running magpie on a box where the
  device-flow URL (the web app on `:3001`) is not reachable.
- One-command quickstart, replacing `make quickstart` / `make local-seed`.
  `curl -fsSL https://openmagpie.ai | sh` clones the repo and runs the make-free
  installer (`scripts/quickstart/run.sh`): it checks Docker, builds the stack,
  migrates, seeds an example feed + watch, and runs one pipeline pass, so a fresh
  machine reaches a real match in one command. In a clone, run
  `./scripts/quickstart/run.sh` directly; `STARTER` / `DAYS` choose a different
  starter or backfill window. Getting-started moved out of `make` into
  `scripts/quickstart/` (POSIX `sh`, Docker the only prerequisite); `make`
  remains the dev-loop interface.
- Webhook deliveries audit: every outbound webhook call is recorded as a
  `WatchActionDelivery` (one row per HTTP attempt) with its state, HTTP status,
  method, item count, redacted target host, and the exact request body sent (no
  headers). View it with `magpie delivery list --action <action_id>` or
  `GET /v1/actions/<action_id>/deliveries`. Each run links to the call that
  carried it via `delivery_id`.
- Webhook actions support an HTTP `method` of `POST`, `PUT`, or `PATCH`
  (default `POST`).

### Changed

- Relevance engine generalized to any OpenAI-compatible `/v1` endpoint (Ollama,
  vLLM, llama.cpp, LM Studio, OpenAI, or a hosted provider), driven by the
  official `openai` client. **Breaking config rename:** `OLLAMA_URL` /
  `OLLAMA_DEFAULT_MODEL` become `ENGINE_BASE_URL` / `ENGINE_MODEL` (plus an
  optional `ENGINE_API_KEY`). To upgrade an existing install, set
  `ENGINE_BASE_URL` (e.g. `http://host.docker.internal:11434/v1`) in
  `apps/core/.env`; a missing value now fails startup with a named
  `ImproperlyConfigured` naming the old and new vars, not a raw `KeyError`.
  `ENGINE_MODEL` is optional (unset still boots, with an `engine.W001` warning).
  A data migration rewrites any stored `semantic_filter` `engine.kind: "ollama"`
  to the server default.
- CLI command tree reshaped so the structure matches how data is used, not ORM
  containment. Observability is now flat and top-level: `magpie activity
  summary` / `list` / `get` and `magpie delivery list` / `get`, each scoped by
  `--action <id>`, REPLACE the buried `magpie watch action activity` /
  `deliveries` / `delivery`. The overloaded `activity` (summary AND run log) is
  split into `activity summary` vs `activity list`. A feed's source set moves
  under a `feed source` sub-noun (`list` / `set` / `export --feed`, `delete` /
  `get <source_id>`, `template`), replacing the `feed *-sources` verbs;
  `watch action list` / `add` take `--watch <id>` instead of a positional. A
  bare positional is now always the resource's own id; a scope is a named flag.
  `delete` is the single destructive verb across every noun (the child-only
  `remove` is gone; `feed source` / `watch action` use `delete` like `feed` /
  `watch`).
  Observability views render a human table by default and emit newline-delimited
  JSON with `--jsonl`. The run audit now shows each item's title + feed name
  (joined server-side) and, for `semantic_filter` actions, the filter score and
  reason.
- New read commands for reviewing definitions: `magpie watch action get
  <action_id>` shows one action's kind + config (it was only addable / settable /
  removable before). A feed's items move to a read-only `feed item` sub-noun
  (`list --feed <id>`, `get <item_id>`; no create / edit / delete), replacing
  `magpie feed view`: `feed get` now shows the feed's definition and `feed item
  list` its content stream, so the two no longer read as synonyms.
- The webhook payload is now one self-describing shape for both instant and
  digest delivery:
  `{watch: {id, name}, action_id, delivery, window, items: [{key, source: {label, kind}, item}]}`
  (instant is a one-item batch with `window` null). Each item now carries the
  source it came from. This REPLACES the previous instant `{action_id, item}`
  and digest `{action_id, items: [{key, item}]}` shapes; receivers must adopt
  the unified shape. Delivery is
  at-least-once; receivers dedup per item on the in-body `key`.
