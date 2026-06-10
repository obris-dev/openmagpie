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

from openmagpie_schema.feed import FeedItemListResponse, FeedItemWire

from ... import console
from ...context import app_ctx
from .._shared import _emit_detail, _emit_list, _handle_api_errors, _print_detail
from ._apps import item_app


def _item_title(item: FeedItemWire) -> str:
    """The item's title from the typed connector payload, or '-' when the
    payload carries none. Title is title: no fallback to external_id / url
    (those are their own fields), so an empty cell honestly means 'no title'
    (the payload's `title` default is ""), not 'unknown shape'."""
    return item.data.title or "-"


@item_app.command("list")
@_handle_api_errors
def list_(
    feed_id: str = typer.Option(..., "--feed", help="Feed id whose items to list."),
    after: str | None = typer.Option(None, "--after", help="Cursor (item id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit one JSON object per item (NDJSON) instead of a table."),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Write to a file instead of stdout; the next cursor prints to stdout."
    ),
) -> None:
    """One feed's items (newest first): the 'sort by new and go' surface."""
    _emit_list(
        fetch=lambda cursor: app_ctx().api.feed.list_items(feed_id, after=cursor, limit=limit),
        after=after,
        render_table=_print_items,
        jsonl_lines=lambda resp: (i.model_dump_json() for i in resp.items),
        jsonl=jsonl,
        output=output,
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


def _print_items(resp: FeedItemListResponse) -> None:
    if not resp.items:
        console.log("No items match.")
        return
    columns: list[console.Column[FeedItemWire]] = [
        console.Column("ITEM ID", lambda i: i.id),
        console.Column("SOURCE", lambda i: i.source_label or "-"),
        console.Column("TITLE", _item_title, width=60),
        console.Column("EXTERNAL ID", lambda i: i.external_id),
        console.Column("OCCURRED", lambda i: str(i.occurred_at) if i.occurred_at else "-"),
    ]
    console.table(resp.items, columns)


def _print_item_detail(i: FeedItemWire) -> None:
    fields: list[tuple[str, str]] = [
        ("source", i.source_label or "-"),
        ("kind", i.source_kind),
        ("external id", i.external_id),
        ("occurred", str(i.occurred_at) if i.occurred_at else "-"),
        ("title", _item_title(i)),
    ]
    _print_detail(f"item {i.id}", fields)
    console.log("\ndata:")
    console.log(i.data.model_dump_json(indent=2))
