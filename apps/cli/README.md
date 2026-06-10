# magpie

The `magpie` CLI, talks to an OpenMagpie server.

## Install (editable, for dev)

```bash
uv sync                # creates .venv with deps from uv.lock
uv run magpie --help
```

To put `magpie` on your global `PATH`:

```bash
uv tool install --editable .
```

## Quickstart

```bash
magpie auth login            # browser device flow
magpie feed create           # opens $EDITOR on a feed template (sources + retention)
magpie watch create          # opens $EDITOR on a watch template (feeds + action chain)
```

Create a feed of sources to watch, then a watch that subscribes to it and runs an action chain over each new item, typically a `semantic_filter` (your plain-English criteria) followed by a `webhook` or `log` delivery. Pick a backfill window when you create the feed and the first `make local-tick` scores real posts against your criteria immediately.

## Commands

| Command | What it does |
|---|---|
| `magpie auth login` / `logout` / `status` | Device-flow sign-in; identity check |
| `magpie feed create` / `list` / `get` / `edit` / `delete` | Curated source streams: `get` shows one feed's definition (config + retention), `list` all feeds |
| `magpie feed source list` / `set` / `export` / `delete` / `get` / `template` | A feed's source set: `list` / `set` / `export` take `--feed <id>`; `delete` / `get` take the source's own id (the feed is resolved + confirmed for you); `template` emits a skeleton |
| `magpie feed item list` / `get` | A feed's items (read-only, server-produced): `list --feed <id>` the recent item stream, `get <item_id>` one item in full. No create / edit / delete |
| `magpie watch create` / `list` / `get` / `edit` / `delete` | Watches over feeds (subscriptions + action chain) |
| `magpie watch action list` / `add` / `get` / `set` / `delete` | A watch's action chain: `list` / `add` take `--watch <id>`; `get` / `set` / `delete` take the action's own id (`get` shows one action's kind + config) |
| `magpie activity summary` / `list` / `get` | One action's run audit, scoped by `--action <id>`: `summary` is a per-state breakdown over a window, `list` the individual run log (`--state` / `--after`), `get <run_id>` one run in full. Works for every action kind (filter, webhook, log); item title + feed name come from the response join, with the filter score + reason shown only for `semantic_filter` actions |
| `magpie delivery list` / `get` | Outbound webhook delivery audit: `list --action <id>` the attempts (state / HTTP / host / items / attempt), `get <delivery_id>` one call in full incl. the exact body sent |
| `magpie feed template` / `watch template` | Emit a config skeleton to stdout |

Observability views (`activity`, `delivery`) render a human table by default and stream newline-delimited JSON with `--jsonl` (one object per row, cursor auto-paginated) for `jq` / piping.

Config lives at `~/.magpie/config.json`.
