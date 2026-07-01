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

from pydantic import BaseModel, Field

# The action-node discriminated unions live in `_nodes` and the audit read-path
# shapes in `_runs` (kept each module under the line cap); re-exported here so
# `from openmagpie_schema.watch import X` keeps working for every consumer.
from ._nodes import (
    ExtractActionInput,
    ExtractActionWire,
    LogActionInput,
    LogActionWire,
    SemanticFilterActionInput,
    SemanticFilterActionWire,
    WatchActionInput,
    WatchActionWire,
    WebhookActionInput,
    WebhookActionWire,
    build_watch_action_input,
    build_watch_action_wire,
    watch_action_input_adapter,
    watch_action_wire_adapter,
)
from ._runs import (
    ResultBlob,
    RunFeed,
    RunFeedItem,
    WatchActionDeliveryListResponse,
    WatchActionDeliveryView,
    WatchActionDeliveryWire,
    WatchActionRunListResponse,
    WatchActionRunSummary,
    WatchActionRunView,
    WatchActionRunWire,
)

# Public API of the watch package (the envelopes defined here + the action-node
# unions re-exported from `_nodes`). Listed so the re-exports aren't seen as
# unused and `from openmagpie_schema.watch import *` stays well-defined.
__all__ = [
    "ExtractActionInput",
    "ExtractActionWire",
    "LogActionInput",
    "LogActionWire",
    "ResultBlob",
    "RunFeed",
    "RunFeedItem",
    "SemanticFilterActionInput",
    "SemanticFilterActionWire",
    "WatchActionDeliveryListResponse",
    "WatchActionDeliveryView",
    "WatchActionDeliveryWire",
    "WatchActionInput",
    "WatchActionMutationResponse",
    "WatchActionRunListResponse",
    "WatchActionRunSummary",
    "WatchActionRunView",
    "WatchActionRunWire",
    "WatchActionWire",
    "WatchInput",
    "WatchListResponse",
    "WatchMutationResponse",
    "WatchView",
    "WatchWire",
    "WebhookActionInput",
    "WebhookActionWire",
    "build_watch_action_input",
    "build_watch_action_wire",
    "watch_action_input_adapter",
    "watch_action_wire_adapter",
]


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


class WatchActionMutationResponse(BaseModel):
    """Single-action add/edit response (POST `/actions` + PUT `/actions/<id>`,
    real and `?dry_run=true`). `dry_run` is True for a validation-only preview;
    `action` is the typed action node (a discriminated union). On a dry-run the
    action isn't persisted, so `action.id` is "" (WatchActionWire can't be
    subclassed to null the id, so the response nests the action rather than
    flattening it as the pre-union shape did)."""

    dry_run: bool
    action: WatchActionWire


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
