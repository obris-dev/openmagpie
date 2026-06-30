"""Rendering for `magpie feed` reads: the `list` columns and the detail field-table.

Split from `_crud` so the command module stays focused on the verbs. Pure display
helpers (no command/IO logic), shared by `list` (columns) and `get`/`create`/`edit`
(the detail table)."""

from __future__ import annotations

from ... import console
from ...api.feed import FeedMutationResponse, FeedView
from .._shared import col

# Default `feed list` columns, as dot-paths into a feed record. POLL appends the
# unit the value can't (`300s`); ACTIVE maps the bool to the same active/paused
# label `feed get` shows, so list + detail agree. `fmt` is the per-cell hook.
_FEED_COLUMNS = [
    col("ID:id"),
    col("NAME:name"),
    col("KIND:kind"),
    col("POLL:poll_interval_seconds", fmt=lambda v: f"{v}s"),
    col("ACTIVE:is_active", fmt=console.active_or_paused),
]


def _sources_value(obj: FeedMutationResponse | FeedView) -> str:
    """The `sources` cell: `(count) name, name, ...`. The table renderer
    truncates the long list to the column cap (a feed with many sources shows the
    count plus a peek, not the whole roster) ; the full list is `feed source list`.
    `(count)` alone when rows aren't echoed (e.g. the create dry-run, which
    reports the would-be count without materializing Source rows) or a 0 feed `(0)`."""
    # SourceWire.spec is the typed SourceSpec union; use `.display()`
    # (every variant implements it) ; `.get(...)` would AttributeError.
    display = ", ".join(s.spec.display() for s in obj.sources)
    return f"({obj.source_count}) {display}" if display else f"({obj.source_count})"


def _print_feed(obj: FeedMutationResponse | FeedView, title: str) -> None:
    """Render a feed's config as a pivoted FIELD | VALUE table (the shared
    list renderer), so it reads like every other view and one long cell
    (sources) truncates instead of blowing out the line."""
    console.header(title)
    rows: list[tuple[str, str]] = [
        ("name", obj.name),
        ("kind", obj.kind),
        ("poll interval", f"{obj.poll_interval_seconds}s"),
        ("sources", _sources_value(obj)),
    ]
    columns: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=16),
        console.Column("VALUE", lambda kv: kv[1], width=64),
    ]
    console.table(rows, columns)
