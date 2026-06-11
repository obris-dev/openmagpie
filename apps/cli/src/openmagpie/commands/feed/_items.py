"""`magpie feed item <verb>`: the feed's items (what polling produced).

Server-produced content and a dependent record of exactly one feed, so it nests
under `feed item` (containment) and is READ-ONLY (no create / edit / delete).
`list` is the feed-scoped "sort by new and go" log (addressed by `--feed`,
cursor-paginated); `get` is one item in full by its own ULID, including the raw
connector payload. Both carry the machine-output modes (`--jsonl` / `-o`) the
observability reads use, since items are content a script or LLM consumes.
"""

from __future__ import annotations

import typer

from openmagpie_schema.feed import FeedItemWire

from ... import console
from ...context import app_ctx
from .._shared import (
    _columns_option,
    _emit_columns_paginated,
    _emit_detail,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _print_detail,
    _transpose_option,
    _ts,
    col,
)
from ._apps import item_app

# Default `feed item list` columns, as `HEADER:dot-path` into an item record
# (`title` lives in the typed payload at `data.title`).
_ITEM_COLUMNS = [
    col("ITEM ID:id"),
    col("SOURCE:source_label"),
    col("TITLE:data.title", width=60),
    col("EXTERNAL ID:external_id"),
    col("OCCURRED:occurred_at", fmt=_ts),
]


def _item_title(item: FeedItemWire) -> str:
    """The item's title from the typed connector payload, or EMPTY when the
    payload carries none. Title is title: no fallback to external_id / url
    (those are their own fields), so an empty cell honestly means 'no title'
    (the payload's `title` default is ""), not 'unknown shape'.

    This is the DETAIL path; `list` projects the same value via `col("TITLE:
    data.title")`. They stay in lockstep on the only divergence-prone case -
    empty -> `console.EMPTY` - because both funnel through the one marker."""
    return item.data.title or console.EMPTY


@item_app.command("list")
@_handle_api_errors
def list_(
    feed_id: str = typer.Option(..., "--feed", help="Feed id whose items to list."),
    after: str | None = typer.Option(None, "--after", help="Cursor (item id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("item"),
    print_columns: bool = _print_columns_option("item"),
    jsonl: bool = _jsonl_rows_option("item"),
    output: str | None = _list_output_option(paginated=True),
) -> None:
    """One feed's items (newest first): the 'sort by new and go' surface."""
    _emit_columns_paginated(
        fetch=lambda cursor, lim: app_ctx().api.feed.list_items(feed_id, after=cursor, limit=lim),
        after=after,
        limit=limit,
        record_of=lambda i, _: i.model_dump(mode="json"),
        default_columns=_ITEM_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No items match.",
    )


@item_app.command("get")
@_handle_api_errors
def get(
    item_id: str = typer.Argument(..., help="Item id (from `magpie feed item list`)."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the item as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one feed item in full, including its raw connector payload."""
    item = app_ctx().api.feed.get_item(item_id)
    _emit_detail(render=lambda: _print_item_detail(item), json_obj=item.model_dump_json, jsonl=jsonl, output=output)


def _print_item_detail(i: FeedItemWire) -> None:
    """Field table + the raw connector payload. The top-level `occurred` is the
    human form (local seconds, via `_ts`, matching the list column); the `data:`
    block re-emits the stored payload verbatim, so its own `occurred_at` keeps the
    canonical ISO string - the same instant in a different surface form, not a
    second source of truth."""
    fields: list[tuple[str, str]] = [
        ("source", i.source_label or console.EMPTY),
        ("kind", i.source_kind),
        ("external id", i.external_id),
        ("occurred", _ts(i.occurred_at)),
        ("title", _item_title(i)),
    ]
    _print_detail(f"item {i.id}", fields)
    console.log("\ndata:")
    console.log(i.data.model_dump_json(indent=2))
