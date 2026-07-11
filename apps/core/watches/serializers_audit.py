"""Watches audit read-path builders: the runs (`activity`) + deliveries wire
shapes.

Split from `serializers.py` (the input + watch-envelope builders) so each module
stays under the line cap and holds one concern; mirrors the `views_audit.py`
split of the audit endpoints. These populate the shared `openmagpie_schema.watch`
models, so the server is their authority and the CLI imports the same classes.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from feeds.models import Feed, FeedItem
from openmagpie_schema.watch import (
    RunFeed,
    RunFeedItem,
    WatchActionDeliveryView,
    WatchActionDeliveryWire,
    WatchActionRunView,
    WatchActionRunWire,
    build_watch_action_run_wire,
)
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.models import WatchAction, WatchActionDelivery, WatchActionRun

from .serializers import watch_action_wire

logger = logging.getLogger("watches")


def run_feed_item_wire(item: FeedItem) -> RunFeedItem:
    """Narrow a FeedItem to the audit log's display fields, for the runs
    response's `feed_items` map (keyed by item id). `feed_id` keys into that
    response's `feeds` map. The view only builds this for items that still
    exist, so a pruned item is simply absent from the map (the run row carries
    `feed_item_id` and renders by it)."""
    data = item.data or {}
    return RunFeedItem(
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        external_url=str(data.get("external_url", "")),
        source_label=str(item.source_label),
        feed_id=str(item.feed_id),
        occurred_at=item.occurred_at,  # a FeedItem column (the occurred_* filter axis), not from data
    )


def run_feed_wire(feed: Feed) -> RunFeed:
    """Narrow a Feed for the runs response's `feeds` map (keyed by feed id).
    Few feeds back the many runs on a page, so this is returned once per feed
    instead of repeated on every item."""
    return RunFeed(id=str(feed.id), name=str(feed.name))


def watch_action_run_wire(run: WatchActionRun) -> WatchActionRunWire | None:
    """One run's wire shape (the audit-log row): pure ids + run state, keyed by
    the run's own `kind` (denormalized, so the row self-describes what ran and the
    union narrows `result` to its exact type). The judged item is in the
    response's `feed_items` map (key `feed_item_id`), its feed in `feeds`.
    `state` coerces to the WatchActionRunState enum; `result` is validated into
    the kind's typed result (None until the run terminates with one).

    A PLUGIN kind's run renders via the PluginRunWire fallback with NO registration
    needed (just kind + result blob), so this deliberately does NOT gate on
    `known_kinds()`: gating there would hide a plugin's entire activity history the
    moment its hook isn't loaded (uninstall, or one replica missing the hooks env), and
    make the audit surface diverge across replicas.

    Per-row fail-safe (a narrowed subset of the `_action_summary` catches: the
    expected raisers here are PydanticValidationError on stored shape drift and
    ValueError on a degenerate stored value; a blanket catch would bury genuine
    bugs as "bad row"). The activity LIST must never 500 on one bad row. A bad `state`,
    or a structurally-corrupt `kind` (blank/padded/over-length, which fails BOTH wire
    branches), can't produce ANY member, so the row is skipped (returns None); a
    malformed `result` degrades to the same kind's member with `result=None` (the
    row still renders its state + ids). The caller drops the None rows."""
    # `state` + `kind` are required with no default, so validate them up front
    # (not in `common`): a legacy/degenerate value skips just this row instead of
    # 500-ing the page, and each is logged with its accurate cause rather than
    # being misattributed to the result by the build below.
    try:
        state = WatchActionRunState(run.state)
    except ValueError:
        logger.exception("run %s has an unrenderable state=%s; skipping the row", run.id, run.state)
        return None
    run_kind = str(run.kind)  # str() for uniformity with the sibling char reads below
    common = {
        "id": str(run.id),
        "watch_id": str(run.watch_id),
        "action_id": str(run.action_id),
        "feed_item_id": str(run.feed_item_id),
        "state": state,
        "error": run.error,
        "scheduled_at": run.scheduled_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "created_at": run.created_at,
    }
    try:
        # The builder coalesces an empty result to None, so forward the raw dict.
        return build_watch_action_run_wire(kind=run_kind, result=run.result, **common)
    except (PydanticValidationError, ValueError):
        # The build failed on the kind OR the result; disambiguate by rebuilding with
        # result=None. A built-in kind, OR any structurally-valid plugin kind, renders
        # there (PluginRunWire, no registration), so success => only the result was
        # malformed: drop it, keep the row (state + ids still render). If this ALSO
        # fails, the kind is structurally corrupt (blank/padded/over-length, which fails
        # both wire branches), the original corrupt-column backstop => skip the row. Both
        # branches log with the outer exception's traceback still in context.
        try:
            base = build_watch_action_run_wire(kind=run_kind, result=None, **common)
        except (PydanticValidationError, ValueError):
            logger.exception("run %s has an unrenderable kind=%s; skipping the row", run.id, run_kind)
            return None
        logger.exception("run %s has a malformed result (kind=%s); dropping it", run.id, run_kind)
        return base


def watch_action_run_view(
    run: WatchActionRun,
    *,
    feed_item: FeedItem | None = None,
    feed: Feed | None = None,
    action: WatchAction | None = None,
) -> WatchActionRunView | None:
    """One run's DETAIL shape (`GET /v1/action-activity/<id>`): the run wire plus
    the joined item / feed / action it was judged against. Each is null when
    absent (a pruned item/feed, a removed action), so the row still renders by
    `run.feed_item_id`. Returns None when the run itself is unrenderable (an
    orphan with an unusable kind); the view turns that into a 404."""
    wire = watch_action_run_wire(run)
    if wire is None:
        return None
    return WatchActionRunView(
        run=wire,
        feed_item=run_feed_item_wire(feed_item) if feed_item is not None else None,
        feed=run_feed_wire(feed) if feed is not None else None,
        action=watch_action_wire(action) if action is not None else None,
    )


def _delivery_fields(delivery: WatchActionDelivery) -> dict[str, Any]:
    """The shared list-row fields of a delivery (everything but the payload).
    The string columns (delivery / method / state) coerce to their enums on
    the wire models."""
    return {
        "id": str(delivery.id),
        "watch_id": str(delivery.watch_id),
        "action_id": str(delivery.action_id),
        "delivery": delivery.delivery,
        "method": delivery.method,
        "state": delivery.state,
        "http_status": delivery.http_status,
        "target_host": delivery.target_host,
        "item_count": delivery.item_count,
        "attempt": delivery.attempt,
        "error": delivery.error,
        "started_at": delivery.started_at,
        "completed_at": delivery.completed_at,
        "created_at": delivery.created_at,
    }


def watch_action_delivery_wire(delivery: WatchActionDelivery) -> WatchActionDeliveryWire:
    """One delivery's LIST-row shape (no request_payload ; see the detail view)."""
    return WatchActionDeliveryWire(**_delivery_fields(delivery))


def watch_action_delivery_view(delivery: WatchActionDelivery) -> WatchActionDeliveryView:
    """One delivery's DETAIL shape: the list row plus the stored request_payload."""
    return WatchActionDeliveryView(**_delivery_fields(delivery), request_payload=delivery.request_payload or {})
