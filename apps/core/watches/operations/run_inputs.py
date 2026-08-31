"""Build the enriched inputs an action's `run` consumes.

Turns the runs + their FeedItems + the owning Watch into the `ActionItem` list
and `ActionContext` the drain (one item) and the flush (a digest batch) hand to
`run()`. READS only (source label/kind off the FeedItem, the watch name) ; the
operations layer owns the writes. Used for EVERY kind ; a filter ignores the
parts it doesn't need.
"""

from __future__ import annotations

from datetime import datetime

from feeds.models import FeedItem
from openmagpie_schema.watch_enums import DeliveryCadence
from watches.actions.protocol import ActionContext, ActionItem
from watches.models import Watch, WatchActionRun
from watches.services import WatchService


def build_run_inputs(
    pairs: list[tuple[WatchActionRun, FeedItem]],
    *,
    watch_id: str,
    delivery: DeliveryCadence,
    window_since: datetime | None = None,
    window_until: datetime | None = None,
) -> tuple[list[ActionItem], ActionContext]:
    """`(items, context)` for one run: one `(run, item)` pair for the drain, N
    for a digest batch."""
    items = [
        ActionItem(
            data=item.data,
            # Identity from the typed, non-null FeedItem columns (NOT the opaque
            # data dump): source_kind + external_id are exactly what the dump's
            # source/external_id held (see feeds._items: source_kind=payload.source).
            key=f"{item.source_kind}:{item.external_id}",
            source_label=item.source_label,
            source_kind=item.source_kind,
            source_meta=item.source_meta,
        )
        for _run, item in pairs
    ]
    context = ActionContext(
        watch_id=watch_id,
        watch_name=_watch_name(watch_id),
        delivery=delivery,
        window_since=window_since,
        window_until=window_until,
    )
    return items, context


def _watch_name(watch_id: str) -> str:
    """The watch's display name for the payload ; empty if it was deleted out
    from under an in-flight run (a benign race, not a failure)."""
    try:
        return WatchService.Global.get(watch_id).name
    except Watch.DoesNotExist:
        return ""
