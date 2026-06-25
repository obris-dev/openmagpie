"""Per-action audit read views: the run log and the delivery log.

Both addressed by the action's own ULID (`/v1/actions/<action_id>/activity` and
`.../deliveries`), newest-first + cursor-paginated, account-scoped. Split from
`views.py` (the watch/action CRUD) to keep each module under the length cap and
focused: this file is read-only audit, that one is mutation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.api_params import parse_limit
from feeds.models import Feed, FeedItem
from feeds.services import FeedItemService, FeedService
from openmagpie_schema.run_windows import RUN_WINDOW_PARAMS, RunWindows, resolve_run_windows
from openmagpie_schema.watch import (
    WatchActionDeliveryListResponse,
    WatchActionRunListResponse,
    WatchActionRunSummary,
)
from openmagpie_schema.watch_enums import (
    WatchActionDeliveryState,
    WatchActionRunState,
    WatchActivityWindow,
    choices,
)

from .api import (
    ActionScopedAPIView,
    WatchActionDeliveryNotFound,
    WatchActionRunNotFound,
    WatchSvcMixin,
)
from .models import WatchAction, WatchActionDelivery, WatchActionRun
from .serializers import (
    run_feed_item_wire,
    run_feed_wire,
    watch_action_delivery_view,
    watch_action_delivery_wire,
    watch_action_run_view,
    watch_action_run_wire,
    watch_action_wire,
)


def _window_bounds(window: WatchActivityWindow, now: datetime) -> tuple[datetime, datetime | None]:
    """Resolve an activity-window preset to concrete `(since, until)` at
    `now` (server clock — one source of truth). `until` is None for rolling
    windows (open-ended to now) ; set only for the calendar 'yesterday'.
    YESTERDAY is a UTC-calendar day (the app runs TIME_ZONE='UTC', `now` is
    UTC), not operator-local. Every enum member is handled explicitly so a
    new one raises here instead of silently falling through to yesterday."""
    if window is WatchActivityWindow.DAY:
        return now - timedelta(hours=24), None
    if window is WatchActivityWindow.WEEK:
        return now - timedelta(days=7), None
    if window is WatchActivityWindow.MONTH:
        return now - timedelta(days=30), None
    if window is WatchActivityWindow.YESTERDAY:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight - timedelta(days=1), midnight
    raise ValueError(f"unhandled window {window!r}")


def _validate_state(raw: str | None, enum: type[StrEnum]) -> Response | None:
    """A 400 Response if `raw` is a non-empty value that isn't a member of
    `enum`, else None. Shared by both audit views' `?state=` filter (each
    passes its own state enum), so the validation reads identically."""
    if raw is None:
        return None
    try:
        enum(raw)
    except ValueError:
        return Response(
            {"state": [f"unknown state {raw!r}; known: {choices(enum)}"]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


def _parse_time_windows(request) -> tuple[RunWindows, Response | None]:
    """Read the run-window query params (the shared `RUN_WINDOW_PARAMS` wire
    contract) into kwargs for `list_for_action`. Each value is a relative duration
    (`7d`) or an absolute ISO datetime, RESOLVED here against the server clock (the
    one source of truth) via the shared `resolve_run_windows`, which also bounds a
    lone `*_until` and rejects an inverted window. Any bad value / inverted window
    is a 400; absent params are omitted (the service treats missing as unbounded)."""
    raw = {name: value for name in RUN_WINDOW_PARAMS if (value := request.query_params.get(name))}
    try:
        return resolve_run_windows(raw, now=timezone.now()), None
    except ValueError as exc:
        # Distinct key from the `--window` preset's 400; the message names the real
        # offending param (occurred_*/completed_*), which is embedded in `exc`.
        return {}, Response({"windows": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)


class ActionRunsView(ActionScopedAPIView):
    """GET /v1/actions/<action_id>/activity: the action's run log ("activity"),
    newest-first, cursor-paginated. `?state=` filters by run state. Account
    scoping is the isolation (the action, and its runs, belong to the caller's
    account), so no watch id is needed in the query."""

    def get(self, request, action_id: str):
        action = self.action  # 404 if absent from this account
        state = request.query_params.get("state") or None
        bad_state = _validate_state(state, WatchActionRunState)
        if bad_state is not None:
            return bad_state
        # Activity-summary window: a bounded preset, resolved to concrete
        # bounds on the server clock. Defaults to WEEK ; scopes only the
        # summary (by evaluation time), never the raw row list.
        window_raw = request.query_params.get("window") or WatchActivityWindow.WEEK.value
        try:
            window = WatchActivityWindow(window_raw)
        except ValueError:
            return Response(
                {"window": [f"unknown window {window_raw!r}; known: {choices(WatchActivityWindow)}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Optional row-window filters (the report export); each an ISO bound on the
        # run's completion or the feed item's source time. Scope only the row list.
        windows, bad_window = _parse_time_windows(request)
        if bad_window is not None:
            return bad_window
        limit = parse_limit(request)
        after = request.query_params.get("after") or None
        # windows' keys ARE the list_for_action kwarg names (the RUN_WINDOW_PARAMS
        # single source); spread rather than re-spell them (a rename can't silently
        # drop a filter via a stale .get()).
        runs = self.run_svc.list_for_action(str(action.id), after=after, limit=limit, state=state, **windows)
        next_cursor = str(runs[-1].id) if len(runs) == limit else None
        items = [watch_action_run_wire(r) for r in runs]
        # Side tables the rows key into (no embedding): the judged feed items for
        # this page by id, then the (few) feeds backing them by id. Two batched
        # fetches, no N+1; a pruned item / feed is simply absent from its map and
        # the row renders by id.
        feed_items = FeedItemService(account_id=request.account_id).get_many([str(r.feed_item_id) for r in runs])
        feed_items_map = {fid: run_feed_item_wire(item) for fid, item in feed_items.items()}
        feeds = FeedService(account_id=request.account_id).get_many({str(item.feed_id) for item in feed_items.values()})
        feeds_map = {fid: run_feed_wire(feed) for fid, feed in feeds.items()}
        # Summary on the first page only (skipped while paging), AND only when no
        # row-window filter is set. A row-windowed request is the export draining
        # rows -- it discards the summary, and the summary is over the `window`
        # PRESET (a different time basis than occurred_*/completed_*), so it'd be
        # both wasted and misleading. The interactive `activity list` sends no
        # row-windows, so it still gets its first-page summary.
        summary = None
        if after is None and not windows:
            since, until = _window_bounds(window, timezone.now())
            summ = self.run_svc.summary_for_action(str(action.id), since=since, until=until)
            summary = WatchActionRunSummary(
                window=window,
                since=since,
                until=until,
                evaluated=summ.evaluated,
                pending=summ.pending,
                running=summ.running,
                retrying=summ.retrying,
            )
        return Response(
            WatchActionRunListResponse(
                items=items,
                next_cursor=next_cursor,
                action=watch_action_wire(action),
                feed_items=feed_items_map,
                feeds=feeds_map,
                summary=summary,
            ).model_dump(mode="json")
        )


class ActionDeliveriesView(ActionScopedAPIView):
    """GET /v1/actions/<action_id>/deliveries: the action's outbound HTTP-call
    log (one row per attempt), newest-first, cursor-paginated. `?state=`
    filters by delivery state. Account scoping is the isolation.

    No summary/window (unlike ActionRunsView): a delivery is a single terminal
    HTTP attempt, so there's no evaluated-vs-backlog breakdown to roll up ;
    don't add one for "consistency"."""

    def get(self, request, action_id: str):
        action = self.action  # 404 if absent from this account
        state = request.query_params.get("state") or None
        bad_state = _validate_state(state, WatchActionDeliveryState)
        if bad_state is not None:
            return bad_state
        limit = parse_limit(request)
        after = request.query_params.get("after") or None
        deliveries = self.delivery_svc.list_for_action(str(action.id), after=after, limit=limit, state=state)
        next_cursor = str(deliveries[-1].id) if len(deliveries) == limit else None
        items = [watch_action_delivery_wire(d) for d in deliveries]
        return Response(WatchActionDeliveryListResponse(items=items, next_cursor=next_cursor).model_dump(mode="json"))


class ActionDeliveryDetailView(WatchSvcMixin, AccountScopedAPIView):
    """GET /v1/action-deliveries/<delivery_id>: one delivery in full, including
    the exact request_payload that was sent. Addressed by the delivery's own
    (globally unique) ULID, account-scoped ; the list (lean rows) lives under
    the action at /v1/actions/<id>/deliveries."""

    def get(self, request, delivery_id: str):
        try:
            delivery = self.delivery_svc.get(delivery_id)
        except WatchActionDelivery.DoesNotExist as exc:
            raise WatchActionDeliveryNotFound(delivery_id) from exc
        return Response(watch_action_delivery_view(delivery).model_dump(mode="json"))


class ActionActivityDetailView(WatchSvcMixin, AccountScopedAPIView):
    """GET /v1/action-activity/<activity_id>: one run ("activity entry") in full,
    with the item it judged, that item's feed, and the action it ran, joined for
    the reader. Addressed by the run's own (globally unique) ULID, account-scoped ;
    the list (lean rows) lives under the action at /v1/actions/<id>/activity."""

    def get(self, request, activity_id: str):
        try:
            run = self.run_svc.get(activity_id)
        except WatchActionRun.DoesNotExist as exc:
            raise WatchActionRunNotFound(activity_id) from exc
        # Join the judged item, its feed, and the action, each best-effort: a
        # pruned item (and thus its feed) is simply absent, as is a removed
        # action. The run row always renders by its own ids regardless.
        try:
            feed_item: FeedItem | None = FeedItemService(account_id=request.account_id).get(str(run.feed_item_id))
        except FeedItem.DoesNotExist:
            feed_item = None
        feed: Feed | None = None
        if feed_item is not None:
            try:
                feed = FeedService(account_id=request.account_id).get(str(feed_item.feed_id))
            except Feed.DoesNotExist:
                feed = None
        try:
            action: WatchAction | None = self.action_svc.get(str(run.action_id))
        except WatchAction.DoesNotExist:
            action = None
        return Response(
            watch_action_run_view(run, feed_item=feed_item, feed=feed, action=action).model_dump(mode="json")
        )
