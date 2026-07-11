"""Display helpers for `magpie watch`: the `list` columns + the detail field-table.

Split from `_crud` so the command module stays focused on the verbs (mirrors
`feed/_render`). The config/error normalizers that once lived here (shared by the read
AND write paths) now live in `_config` under a truer name; this module is display-only
(the `_print_*` helpers render to the console)."""

from __future__ import annotations

from ... import console
from ...api.watch import WatchActionWire, WatchMutationResponse, WatchView
from .._shared import col

# Default `watch list` columns, as dot-paths into a watch record. ACTIVE maps the
# bool to the same active/paused label `watch get` shows; FEEDS is a list of feed
# ids the renderer joins with `, ` (scalars), not a JSON array. Empty cells render
# `-` on both surfaces (the uniform table convention; `get` aligns to it too).
_WATCH_COLUMNS = [
    col("ID:id"),
    col("NAME:name"),
    col("ACTIVE:is_active", fmt=console.active_or_paused),
    col("FEEDS:feed_ids"),
]


def _print_watch(obj: WatchMutationResponse | WatchView, title: str) -> None:
    """Render a watch's config as a pivoted FIELD | VALUE table (the shared
    list renderer, matching feed get), then the action chain as its own
    table. is_active rides in the title, so it's not repeated as a row."""
    console.header(title)
    config_rows: list[tuple[str, str]] = [
        ("name", obj.name),
        ("feeds", ", ".join(obj.feed_ids) or console.EMPTY),
        ("chain", f"{len(obj.actions)} action(s)"),
    ]
    config_columns: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=16),
        # Uncapped: `feeds` is comma-joined feed ids, and there is no other
        # command that lists a watch's feed ids in full, so hiding them behind
        # an ellipsis strands the user. (Unlike feed get's `sources`, which is
        # a deliberate summary backed by `feed source list`.)
        console.Column("VALUE", lambda kv: kv[1], width=0),
    ]
    console.table(config_rows, config_columns)
    if not obj.actions:
        return
    console.log("")  # blank line between the config + chain tables
    chain_columns: list[console.Column[WatchActionWire]] = [
        console.Column("RANK", lambda a: str(a.rank)),
        console.Column("KIND", lambda a: a.kind),
        console.Column("SUMMARY", lambda a: a.summary.detail or console.EMPTY),
    ]
    console.table(obj.actions, chain_columns)
