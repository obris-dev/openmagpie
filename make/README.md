# make commands

The dev loop runs through `make`. Below is the curated set you'll reach for most;
run `make help` for the complete, always-current list (it's generated from the
targets, so it never goes stale as commands are added or renamed).

## Stack

- `make build`: build the images and start the stack
- `make up` / `make down`: start / stop the stack
- `make logs` (or `logs-core` / `logs-web`): tail container logs

## Dev loop

- `make local-migrate`: apply migrations + ensure the cache table + bootstrap the CLI OAuth app
- `make local-lint` / `local-lint-fix`: ruff + whitespace (check / auto-fix)
- `make local-types`: ty type check
- `make local-test`: run the Django test suite
- `make local-check`: lint + types + tests (the pre-push habit)

> [!NOTE]
> With the pre-commit hooks installed (`make hooks`), ruff,
> formatting, whitespace, and ty run on **every commit**, so the lint/type targets
> above are mainly a faster manual loop. Tests are not part of the pre-commit hooks.

## Pipeline

- `make local-tick`: run one pipeline pass now (poll -> trigger -> drain -> flush)
- `make up-jobs` / `make down-jobs`: start / stop the background tickers (per-stage cadence; override inline, e.g. `make up-jobs DRAIN_INTERVAL=30`)

## CLI

- `make install-local-cli`: put the local `magpie` CLI on your PATH (needs uv)
- `make local-cli ARGS="..."`: run the CLI via uv without installing it (e.g. `ARGS="watch create"`)

Anything not listed here: `make help`. CLI usage lives in the
[magpie CLI reference](../apps/cli/README.md); contribution flow in
[CONTRIBUTING.md](../CONTRIBUTING.md).
