"""Audit read-path shapes: WatchActionRun (activity) + WatchActionDelivery.

Split from the watch package's envelopes so each module holds one concern and
stays under the line cap. These are the narrowed projections the activity /
deliveries endpoints return; the judged item / feed live in the response's side
tables (see the per-model docstrings)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..watch_enums import (
    DeliveryCadence,
    WatchActionDeliveryState,
    WatchActionRunState,
    WatchActivityWindow,
    WebhookMethod,
)
from ._nodes import WatchActionWire

ResultBlob = dict[str, Any]
"""A WatchActionRun's kind-specific `result`, opaque on the wire.

The runner writes a kind-strict result (see `watch_actions`); readers
carry it verbatim and render common keys best-effort."""


# ── ActionRun (audit log read path) ───────────────────────────────────────


class RunFeedItem(BaseModel):
    """The feed item a run was judged against, narrowed for the audit log.

    The `Run*` prefix (vs the package's `*Wire` suffix) marks an audit-narrowed
    projection: a deliberately smaller view of `FeedItem` for the runs response's
    side tables, not the full `FeedItemWire`. `RunFeed` is the same idea for Feed.

    `title` / `url` / `external_url` come from the connector payload
    (`FeedItem.data`, a SourcePayload dump where all three live on the base ;
    `external_url` is the off-site link, empty when there's none). `source_label`
    is the operator-visible source string. `feed_id` keys into the response's `feeds`
    map and is REQUIRED (the structural join key; the display fields default to
    empty, the join key never does). NOT embedded on each run row: returned once
    per item in the response's `feed_items` map (items are ~1:1 with runs, but the
    run row stays pure ids and the shape matches `action` / `feeds`)."""

    feed_id: str
    title: str = ""
    url: str = ""
    external_url: str = ""
    source_label: str = ""
    # The item's SOURCE time (the `occurred_*` window filters on it), so a row can be
    # sorted / verified on the axis it was filtered by. A FeedItem COLUMN (not from
    # `data`); nullable, like the column.
    occurred_at: datetime | None = None


class RunFeed(BaseModel):
    """A feed referenced by the audited runs, returned once in the response's
    `feeds` map (keyed by id). Feeds are far fewer than runs, so this normalizes
    the many runs -> few feeds relationship instead of repeating the feed per
    row."""

    id: str
    name: str = ""


class WatchActionRunWire(BaseModel):
    """One WatchActionRun on the wire (`GET /v1/actions/<action_id>/activity`).

    The stateful audit row of one action executing against one item. Pure ids +
    run state: the judged item is in the response's `feed_items` map (key
    `feed_item_id`), the feed in `feeds` (key `feed_items[feed_item_id].feed_id`).
    `result` is the kind-specific output blob (opaque; render common keys
    best-effort). `state` is the `WatchActionRunState` value. Datetimes real;
    renderer encodes."""

    id: str
    watch_id: str
    action_id: str
    feed_item_id: str
    state: WatchActionRunState
    result: ResultBlob = Field(default_factory=dict)
    error: str = ""
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class WatchActionRunSummary(BaseModel):
    """Activity over an action's runs, so an operator sees "what is this
    action doing?" without scrolling the log.

    `evaluated` is the per-terminal-state breakdown of runs JUDGED within
    [since, until) -- windowed on completion (evaluation) time, not enqueue
    time. `pending` / `running` / `retrying` are the CURRENT live backlog,
    NOT time-bound (those runs have no completion time yet), surfaced so the
    queue stays visible. `window` is the requested preset ; `since` / `until`
    are the concrete bounds the server resolved it to.

    DISJOINT by `completed_at`: a FAILED run is counted in `evaluated[failed]`
    iff it's terminal (attempts exhausted -> has a completion time) and in
    `retrying` iff it's not (still under the cap -> no completion time). So a
    consumer can sum across buckets without double-counting."""

    window: WatchActivityWindow
    since: datetime
    until: datetime | None = None
    # Keyed by the run-state enum (identical on the wire -- StrEnum serializes
    # to its value -- but typed for the CLI, closing the "state magic strings"
    # door). Backlog states never appear here (they have no completion time).
    # An `evaluated[failed]` count is the EXHAUSTED (terminal) failures only.
    evaluated: dict[WatchActionRunState, int] = Field(default_factory=dict)
    # Live backlog (not time-bound): pending/running haven't reached a resting
    # state ; retrying is a transient FAILED still under the attempts cap (no
    # completed_at), distinct from the exhausted failures in `evaluated`.
    pending: int = 0
    running: int = 0
    retrying: int = 0


class WatchActionRunListResponse(BaseModel):
    """`GET /v1/actions/<action_id>/activity` envelope. Cursor-paginated by ULID
    pk, newest-first. `?after=<id>` for the next page; `next_cursor` null
    when the page wasn't full. Filter by `?state=` (a WatchActionRunState
    value). `summary` (the full per-state breakdown) is present on the
    first page only (omitted when paging with `?after=`)."""

    items: list[WatchActionRunWire] = Field(default_factory=list)
    next_cursor: str | None = None
    # The action being audited (kind + config), so a reader sees WHAT the runs
    # were judged against (e.g. a semantic_filter's instructions + threshold) as
    # a header. Constant per page; the action row is already loaded server-side.
    action: WatchActionWire | None = None
    # Side tables the run rows key into instead of embedding (a run carries only
    # `feed_item_id`): `feed_items` by feed_item_id (the judged item's title/url),
    # `feeds` by feed_id (its feed). Pruned items are simply absent. Normalizes
    # many runs -> few feeds; a missing key renders by id.
    feed_items: dict[str, RunFeedItem] = Field(default_factory=dict)
    feeds: dict[str, RunFeed] = Field(default_factory=dict)
    # None means "this is a paged response" (no summary computed) -- NOT "no
    # activity". The first page always carries a summary, all-zero if idle.
    summary: WatchActionRunSummary | None = None


class WatchActionRunView(BaseModel):
    """`GET /v1/action-activity/<id>`: one run ("activity entry") in full, with
    the joined item / feed / action so a reader sees WHAT it judged and under
    WHICH action without a second call. `run` is NESTED (not inherited the way
    WatchActionDeliveryView extends its wire) because the item / feed / action are
    peer joins onto the run, not extra columns on it. The list returns the same
    join as keyed side tables; the detail inlines the one item + feed it needs.
    `feed_item` / `feed` are null when the item has been pruned by retention (the
    run still renders by `run.feed_item_id`); `action` is null only if it was
    removed."""

    run: WatchActionRunWire
    feed_item: RunFeedItem | None = None
    feed: RunFeed | None = None
    action: WatchActionWire | None = None


# ── Delivery (outbound HTTP call audit read path) ─────────────────────────


class WatchActionDeliveryWire(BaseModel):
    """One WatchActionDelivery on the LIST wire
    (`GET /v1/actions/<action_id>/deliveries`).

    One outbound HTTP call ATTEMPT (a digest that retries makes several, one
    row each). `target_host` is the redacted destination host (never the full
    URL or any secret). `http_status` is null until the call returns. The full
    `request_payload` is NOT here (it can be a large batch body) ; fetch one
    delivery's detail (WatchActionDeliveryView) for it. Datetimes real; renderer
    encodes."""

    id: str
    watch_id: str
    action_id: str
    delivery: DeliveryCadence
    method: WebhookMethod
    state: WatchActionDeliveryState
    http_status: int | None = None
    target_host: str = ""
    item_count: int = 0
    attempt: int = 0
    error: str = ""
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class WatchActionDeliveryView(WatchActionDeliveryWire):
    """`GET /v1/action-deliveries/<delivery_id>`, the detail: the list row plus the
    exact `request_payload` we sent (a WebhookPayload dump), stored
    point-in-time. Opaque here ; headers are NEVER included (auth tokens). Kept
    off the list wire so a list call doesn't ship every batch body."""

    request_payload: dict[str, Any] = Field(default_factory=dict)


class WatchActionDeliveryListResponse(BaseModel):
    """`GET /v1/actions/<action_id>/deliveries` envelope. Cursor-paginated by
    ULID pk, newest-first. `?after=<id>` for the next page; `next_cursor` null
    when the page wasn't full. Filter by `?state=` (a WatchActionDeliveryState
    value)."""

    items: list[WatchActionDeliveryWire] = Field(default_factory=list)
    next_cursor: str | None = None
