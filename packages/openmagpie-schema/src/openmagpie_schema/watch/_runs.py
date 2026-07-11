"""Audit read-path shapes: WatchActionRun (activity) + WatchActionDelivery.

Split from the watch package's envelopes so each module holds one concern and
stays under the line cap. These are the narrowed projections the activity /
deliveries endpoints return; the judged item / feed live in the response's side
tables (see the per-model docstrings)."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from .._unions import _PLUGIN_MEMBER_LOC_NAMES, KIND_MAX_LENGTH, builtin_union_kinds, reject_builtin_kind
from ..watch_actions import (
    ExtractResult,
    LogResult,
    SemanticFilterResult,
    WebhookResult,
)
from ..watch_enums import (
    BUILTIN_ACTION_KINDS,
    DeliveryCadence,
    WatchActionDeliveryState,
    WatchActionKind,
    WatchActionRunState,
    WatchActivityWindow,
    WebhookMethod,
)
from ._nodes import WatchActionWire

# Built-in action kinds: the shared exported set (not the sibling module's private
# copy, and not a third local re-derivation).
_BUILTIN_KINDS = BUILTIN_ACTION_KINDS

ResultBlob = dict[str, Any]
"""The raw kind-specific `result` dict as persisted on WatchActionRun.result.

The typed per-kind projection is `SemanticFilterResult` / `ExtractResult` /
`LogResult` / `WebhookResult`, carried on the wire by the run union below; this
alias is the pre-validation input the server hands the builder."""


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


# A run row is a discriminated union keyed by `kind` (the run's action kind,
# denormalized onto the row so a reader knows WHAT ran and the union narrows
# `result` to its exact type). `result` is the PURE typed per-kind output,
# optional for two distinct reasons: a pending / running / errored run has
# produced no result yet, AND the result shapes can't fall back on an empty
# default instance (some carry required fields, e.g. score / http_status). Hence
# `| None`, not a default instance.
class WatchActionRunFields(BaseModel):
    """Kind-independent fields on a WatchActionRun audit row.

    The stateful audit row of one action executing against one item. Pure ids +
    run state: the judged item is in the response's `feed_items` map (key
    `feed_item_id`), the feed in `feeds` (key `feed_items[feed_item_id].feed_id`).
    `state` is the `WatchActionRunState` value. Datetimes real; renderer
    encodes."""

    id: str
    watch_id: str
    action_id: str
    feed_item_id: str
    state: WatchActionRunState
    error: str = ""
    scheduled_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class SemanticFilterRunWire(WatchActionRunFields):
    kind: Literal[WatchActionKind.SEMANTIC_FILTER] = WatchActionKind.SEMANTIC_FILTER
    result: SemanticFilterResult | None = None


class ExtractRunWire(WatchActionRunFields):
    kind: Literal[WatchActionKind.EXTRACT] = WatchActionKind.EXTRACT
    result: ExtractResult | None = None


class LogRunWire(WatchActionRunFields):
    kind: Literal[WatchActionKind.LOG] = WatchActionKind.LOG
    result: LogResult | None = None


class WebhookRunWire(WatchActionRunFields):
    kind: Literal[WatchActionKind.WEBHOOK] = WatchActionKind.WEBHOOK
    result: WebhookResult | None = None


class PluginRunWire(WatchActionRunFields):
    """Fallback run member for a plugin (non-built-in) action kind. `result` is an
    untyped blob (a fork's typed result schema lives in its own contract). Selected
    only when no built-in discriminator matches (left-to-right union)."""

    kind: str = Field(min_length=1, max_length=KIND_MAX_LENGTH)
    result: dict[str, Any] | None = None

    @field_validator("kind")
    @classmethod
    def _not_builtin(cls, v: str) -> str:
        return reject_builtin_kind(v, _BUILTIN_KINDS)


# One WatchActionRun on the wire (`GET /v1/actions/<action_id>/activity`), keyed
# by `kind`: the four built-ins as a discriminated union, then a left-to-right
# fallthrough to the plugin member (same discipline as WatchActionWire).
_BuiltinWatchActionRunWire = Annotated[
    SemanticFilterRunWire | ExtractRunWire | LogRunWire | WebhookRunWire,
    Field(discriminator="kind"),
]
WatchActionRunWire = Annotated[_BuiltinWatchActionRunWire | PluginRunWire, Field(union_mode="left_to_right")]

# Import-time parity guard (see _nodes.py): the run union's built-in members must be
# exactly the WatchActionKind enum, so _BUILTIN_KINDS can't drift from what it
# discriminates. Loud at import for a fork; the activity-failsafe test also pins it.
if builtin_union_kinds(_BuiltinWatchActionRunWire) != _BUILTIN_KINDS:
    raise RuntimeError(
        f"WatchActionRunWire built-in members {sorted(builtin_union_kinds(_BuiltinWatchActionRunWire))} != "
        f"WatchActionKind {sorted(_BUILTIN_KINDS)}"
    )
# Pin the fallback member name against `_unions._PLUGIN_MEMBER_LOC_NAMES` (see _nodes).
if PluginRunWire.__name__ not in _PLUGIN_MEMBER_LOC_NAMES:
    raise RuntimeError(f"{PluginRunWire.__name__} missing from _unions._PLUGIN_MEMBER_LOC_NAMES")

# The union is a type alias, not a class, so it has no `.model_validate`; this
# adapter is the single validation entry (the server builder + the CLI response
# parsing go through it). Keys on the sibling `kind`.
watch_action_run_wire_adapter: TypeAdapter[WatchActionRunWire] = TypeAdapter(WatchActionRunWire)


def build_watch_action_run_wire(
    *,
    kind: WatchActionKind | str,
    id: str,
    watch_id: str,
    action_id: str,
    feed_item_id: str,
    state: WatchActionRunState | str,
    result: ResultBlob | None = None,
    error: str = "",
    scheduled_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    created_at: datetime | None = None,
) -> WatchActionRunWire:
    """Build a WatchActionRunWire union member from its parts. `kind` selects the
    member ; `result` is the raw persisted result dict (pure, no kind), validated
    into that member's typed result.

    An empty result ({}) becomes None (a run with no terminal result yet). That
    coalesce relies on a REAL result dump never being {}, true because every
    result model has at least one field, so its dump always has at least one key.

    A result whose shape doesn't match `kind` raises ONLY for members with a
    required field (semantic_filter's score, webhook's http_status); the server's
    per-row fail-safe then degrades to null. The all-defaulted members (extract,
    log ; extra='ignore') instead ABSORB a mismatched blob into a defaulted
    instance (silent, no raise), so `kind` MUST be the kind that actually produced
    the result (stamped at enqueue, re-stamped at completion) or a cross-kind
    mismatch would render as wrong data rather than degrade."""
    payload: dict[str, Any] = {
        "kind": kind,
        "id": id,
        "watch_id": watch_id,
        "action_id": action_id,
        "feed_item_id": feed_item_id,
        "state": state,
        "result": result or None,
        "error": error,
    }
    # Omit unset timestamps so each member's own default applies (mirrors
    # build_watch_action_wire's conditional handling of its optional fields).
    for name, value in (
        ("scheduled_at", scheduled_at),
        ("started_at", started_at),
        ("completed_at", completed_at),
        ("created_at", created_at),
    ):
        if value is not None:
            payload[name] = value
    return watch_action_run_wire_adapter.validate_python(payload)


class WatchActionRunSummary(BaseModel):
    """Activity over an action's runs, so an operator sees "what is this
    action doing?" without scrolling the log.

    `evaluated` is the per-terminal-state breakdown of runs JUDGED within
    [since, until), windowed on completion (evaluation) time, not enqueue
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
    # Keyed by the run-state enum (identical on the wire, StrEnum serializes to
    # its value, but typed for the CLI, closing the "state magic strings"
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
    # None means "this is a paged response" (no summary computed), NOT "no
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
