"""Watch API wire shapes (the server-emitted response envelopes) + the
write-side input envelopes the CLI constructs.

SHARED, zero-Django source of truth. The server builds every `/v1/watches`
response THROUGH these (server is the authority) ; the magpie CLI imports
the SAME classes and validates responses against them, so there's no
hand-mirrored copy to drift. The per-kind action `config` / `result` blobs
stay opaque here (`ConfigBlob` / `ResultBlob`) ; their strict shapes live
in `watch_actions.py`, validated server-side by a kind-keyed registry.

Mirrors `feed.py` (envelope quartet: Wire / ListResponse / View /
MutationResponse) deliberately, so the two primitives read the same.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .watch_actions import WatchActionConfigSummary
from .watch_enums import (
    DeliveryCadence,
    WatchActionDeliveryState,
    WatchActionRunState,
    WatchActivityWindow,
    WebhookMethod,
)
from .wire import ConfigBlob

ResultBlob = dict[str, Any]
"""A WatchActionRun's kind-specific `result`, opaque on the wire.

The runner writes a kind-strict result (see `watch_actions`); readers
carry it verbatim and render common keys best-effort."""


# ── Action chain (nested under a watch's path) ────────────────────────────


class WatchActionWire(BaseModel):
    """One action node on the wire. `config` is the kind-specific blob
    (opaque here; the server validated it via the registry on write).
    `summary` is the server-built display projection so the CLI never
    parses `config`."""

    id: str
    kind: str
    rank: int
    config: ConfigBlob = Field(default_factory=dict)
    summary: WatchActionConfigSummary = WatchActionConfigSummary()
    created_at: datetime | None = None


class WatchActionInput(BaseModel):
    """One action on a create / edit / add-action request: `{id?, kind,
    config}` with `kind` adjacent to its blob (k8s-style). `kind` selects
    the action type ; the server validates `config` against it via the
    registry, so the persisted blob is the pure kind-specific shape (no
    `kind` nested inside). `rank` is optional on input (append when
    omitted); the server owns the dense renumber. Extra keys ignored so an
    edit seed's read-only fields drop on round-trip.

    `id` is the STABLE identity of an existing action, carried back on a
    whole-chain edit so the server matches by id (NOT list position):
    matched actions are updated in place ; their id + run history survive,
    and a masked secret restores from that same row. Omit `id` (or leave it
    empty) for a brand-new action ; the server mints its id. A non-empty id
    that isn't on the watch is rejected."""

    id: str = ""
    kind: str
    config: ConfigBlob = Field(default_factory=dict)
    rank: int | None = None

    model_config = {"extra": "ignore"}


# ── Watch envelope (read path) ────────────────────────────────────────────


class WatchWire(BaseModel):
    """The envelope every `/v1/watches` response item carries. List-item
    shape and base for the detail / mutation responses.

    `feed_ids` is the watch's subscription set (the WatchFeed rows,
    minus the internal per-feed watermark which never crosses the wire).
    Datetimes are real `datetime` (None pre-save); JSON encoding is the
    renderer's job. `user_id` is creator/audit only (account-scoped
    reads, not an ownership filter)."""

    id: str
    name: str
    is_active: bool
    feed_ids: list[str] = Field(default_factory=list)
    user_id: str
    created_at: datetime | None = None


class WatchListResponse(BaseModel):
    """`GET /v1/watches` -> `{"items": [...], "next_cursor": <id>|None}`.

    Cursor-paginated by ULID pk, newest-first. Pass `?after=<id>` for the
    next page; `next_cursor` is the id to send back, or null when the
    page wasn't full (no more rows)."""

    items: list[WatchWire] = Field(default_factory=list)
    next_cursor: str | None = None


class WatchView(WatchWire):
    """`GET /v1/watches/<id>`, the read view: the envelope plus the
    ordered action chain of the watch's initial path. v1 has exactly one
    path, so `actions` is that path's actions by `rank` ; the path layer
    stays hidden on the wire until multi-path ships."""

    actions: list[WatchActionWire] = Field(default_factory=list)


class WatchMutationResponse(WatchWire):
    """Create / edit response (POST + PUT, real and `?dry_run=true`).
    `id` is absent on a create dry-run preview (server omits the pre-save
    placeholder), hence `str | None`. `dry_run` is True for a
    validation-only preview. Carries the same `actions` enrichment as
    WatchView so the CLI's confirm-preview shows the resulting chain."""

    id: str | None = None
    actions: list[WatchActionWire] = Field(default_factory=list)
    dry_run: bool


class WatchInput(BaseModel):
    """The envelope the CLI constructs for a watch write (request side).
    CLI-owned, distinct from the server-emitted models. `feed_ids` is the
    subscription set; `actions` is the initial path's ordered chain. The
    server creates the Watch + its single WatchPath + WatchFeed rows
    atomically. Extra keys ignored so an edit seed's read-only fields
    drop on round-trip."""

    name: str
    is_active: bool = True
    feed_ids: list[str] = Field(default_factory=list)
    actions: list[WatchActionInput] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


# ── ActionRun (audit log read path) ───────────────────────────────────────


class RunFeedItem(BaseModel):
    """The feed item a run was judged against, narrowed for the audit log.

    `title` / `url` come from the connector payload (`FeedItem.data`, a
    SourcePayload dump where both fields live on the base). `source_label` is the
    operator-visible source string. `feed_id` keys into the response's `feeds`
    map. NOT embedded on each run row: returned once per item in the response's
    `feed_items` map (items are ~1:1 with runs, but the run row stays pure ids and
    the shape matches `action` / `feeds`)."""

    title: str = ""
    url: str = ""
    source_label: str = ""
    feed_id: str = ""


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
    [since, until) — windowed on completion (evaluation) time, not enqueue
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
    # Keyed by the run-state enum (identical on the wire — StrEnum serializes
    # to its value — but typed for the CLI, closing the "state magic strings"
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
    # None means "this is a paged response" (no summary computed) — NOT "no
    # activity". The first page always carries a summary, all-zero if idle.
    summary: WatchActionRunSummary | None = None


class WatchActionRunView(BaseModel):
    """`GET /v1/action-activity/<id>` — one run ("activity entry") in full, with
    the joined item / feed / action so a reader sees WHAT it judged and under
    WHICH action without a second call. The list returns the same join as keyed
    side tables; the detail inlines the one item + feed it needs. `feed_item` /
    `feed` are null when the item has been pruned by retention (the run still
    renders by `run.feed_item_id`); `action` is null only if it was removed."""

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
