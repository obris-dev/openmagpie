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
| `magpie feed list-sources` / `set-sources` / `remove-source` / `template-sources` / `export-sources` | Manage a feed's source set |
| `magpie watch create` / `list` / `get` / `edit` / `delete` | Watches over feeds (subscriptions + action chain) |
| `magpie watch action add` / `list` / `set` / `remove` | Edit a watch's action chain |
| `magpie watch action activity <action_id>` | Per-state activity summary for one action (`--list` for the run log, `--state` / `-w <window>` filters) |
| `magpie watch action deliveries <action_id>` / `delivery <id>` | Outbound delivery audit: the list of webhook attempts (state / HTTP / host / items / attempt), and one call in full incl. the exact body sent |
| `magpie feed template` / `watch template` | Emit a config skeleton to stdout |

Config lives at `~/.magpie/config.json`.
