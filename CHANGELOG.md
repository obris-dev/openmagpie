# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] - 2026-06-09

### Added

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
