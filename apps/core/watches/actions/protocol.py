"""The Action interface: what it means to RUN one WatchAction against feed
item(s), and the typed inputs/outputs it works with.

Distinct from the CONFIG layer (`watches.registry`, kind -> Pydantic config
class, validation). This is the EXECUTION layer: kind -> runnable impl. The
drain/flush look up the impl for a run's `kind` and call ONE method, `run`,
for every kind (filter or delivery) ; uniform dispatch, no branching. A
filter judges one item ; a delivery emits one (instant) or many (digest).

Module per capability: `protocol.py` (this), `registry.py` (kind -> impl),
`semantic_filter.py` / `webhook.py` / `log.py` (the impls). `_config.py`
holds the shared typed-config loader ; `_fetch.py` the `ExternalFetchMixin`
(the SSRF-safe way an action fetches an arbitrary / user-supplied URL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from watches.models import WatchAction


@dataclass(frozen=True)
class ActionItem:
    """One enriched item handed to an action: the FeedItem's stored `data`
    dump, its stable `key` (source:external_id), and the originating source
    (`source_label` / `source_kind` from the FeedItem row). A filter reads
    `data` and ignores the rest ; a delivery uses all of it. NOT the wire body:
    a delivery narrows `data` by `include_fields` into what it sends. (Upstream
    run results, e.g. the filter score, are deliberately NOT here ; that
    run-chain provenance is a separate opt-in enrichment, walked only for
    deliveries that request it.)"""

    data: dict
    key: str
    source_label: str
    source_kind: str


@dataclass(frozen=True)
class ActionContext:
    """Call-level context shared by every item in one run: which watch (id +
    name, so a receiver can label the listener), the delivery cadence, and the
    digest window bounds (both None for instant / filters)."""

    watch_id: str
    watch_name: str
    delivery: DeliveryCadence
    window_since: datetime | None = None
    window_until: datetime | None = None


@dataclass(frozen=True)
class OutboundCall:
    """The record of ONE outbound HTTP attempt, carried on an
    `OutboundActionResult` so the operations layer can persist a
    WatchActionDelivery row. `request_payload` is the exact body sent (no
    headers). `http_status` is None when no response arrived (blocked /
    connect error)."""

    target_host: str
    method: str
    http_status: int | None
    item_count: int
    request_payload: dict


@dataclass(frozen=True)
class ActionResult:
    """How a run resolved, for EVERY kind. The drain/flush persist `state` +
    `result` onto the WatchActionRun and advance the chain IFF
    `state == SUCCEEDED`.

    - `state`: SUCCEEDED (advance), GATED (clean stop, a filter pass=false),
      ERRORED (a permanent defect the impl detected: unhydrateable item /
      invalid config / unknown engine / blocked destination), or FAILED (a
      TRANSIENT failure the impl already classified, e.g. a 5xx / connect
      error from a delivery; retryable). Filters never return FAILED (the
      drain sets it on an UNEXPECTED raise) ; deliveries DO, so the failed
      attempt is still recorded. SKIPPED is reserved (deliberate non-run).
    - `result`: the kind-specific result blob (validated per kind), stored on
      the run for the audit log (a semantic filter writes `SemanticFilterResult`).
    - `error`: operator-facing note on a non-clean terminal state. Empty for
      SUCCEEDED / GATED. Sanitized ; the raw cause goes to the logs (see
      `watches.run_messages`)."""

    state: WatchActionRunState
    result: dict = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class OutboundActionResult(ActionResult):
    """An `ActionResult` from an action that made an outbound HTTP call (the
    webhook kind). Adds the `outbound` record so the operations layer logs a
    WatchActionDelivery and links the run(s) to it. The persistence path checks
    `isinstance(result, OutboundActionResult)` ; everything else reads the base
    fields, so dispatch stays uniform. `outbound` is keyword-only so it can
    follow the base's defaulted fields without a default of its own (it is
    always present on an outbound result)."""

    outbound: OutboundCall = field(kw_only=True)


class Action(Protocol):
    """A runnable action kind. Declares its `kind` (matches a `WatchActionKind`
    value) and runs against one item (instant / filter) or many (digest) via
    ONE method. Returns an `ActionResult` (or an `OutboundActionResult` for a
    kind that made an HTTP call) ; raises only on UNEXPECTED failure (the
    drain/flush map that to a retryable FAILED). `context` is ignored by kinds
    that don't need it (filters)."""

    kind: str

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult: ...
