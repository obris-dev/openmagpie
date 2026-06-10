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
| `magpie feed create` / `list` / `get` / `view` / `edit` / `delete` | Curated source streams (the set a watch subscribes to) |
| `magpie feed source list` / `set` / `export` `--feed <id>` · `remove` / `get <source_id>` · `template` | A feed's source set: `--feed` scopes the list / bulk set / export; `remove` and `get` take the source's own id (the feed is resolved + confirmed for you) |
| `magpie watch create` / `list` / `get` / `edit` / `delete` | Watches over feeds (subscriptions + action chain) |
| `magpie watch action list` / `add` `--watch <id>` · `set` / `remove <action_id>` | A watch's action chain: `--watch` scopes the list / add; `set` and `remove` take the action's own id |
| `magpie activity summary` / `list` `--action <id>` · `get <run_id>` | One action's run audit: a per-state breakdown over a window (`summary`), the individual run log (`list`, with `--state` / `--after` filters), or one run in full (`get`). `--action` scopes summary / list; item title + feed name come from the response join, with the filter score + reason shown for `semantic_filter` actions |
| `magpie delivery list --action <id>` / `get <delivery_id>` | Outbound webhook delivery audit: the list of attempts (state / HTTP / host / items / attempt), and one call in full incl. the exact body sent |
| `magpie feed template` / `watch template` | Emit a config skeleton to stdout |

Observability views (`activity`, `delivery`) render a human table by default and stream newline-delimited JSON with `--jsonl` (one object per row, cursor auto-paginated) for `jq` / piping.

Config lives at `~/.magpie/config.json`.
