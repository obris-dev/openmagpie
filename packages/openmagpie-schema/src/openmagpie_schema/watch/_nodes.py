"""Action-node wire + input shapes: the per-kind discriminated unions.

Split from the watch package's envelopes so each module stays under the line
cap and holds one concern. `config` is the PURE typed kind-specific shape (NO
`kind` nested inside); `kind` is the sibling discriminator the union keys on.
The server + the CLI + the web all generate from these, so `switch (action.kind)`
narrows `config` to its exact type.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from ..watch_actions import (
    ExtractConfig,
    LogConfig,
    SemanticFilterConfig,
    WatchActionConfigSummary,
    WebhookConfig,
)
from ..watch_enums import WatchActionKind

# An action node is a discriminated union keyed by `kind`. `config` is the PURE
# kind-specific typed shape (NO `kind` nested inside; `kind` is the sibling
# discriminator the union keys on). This is the single typed source both the CLI
# and the web generate from, so `switch (action.kind)` narrows `config` to its
# exact type. The server validates + builds these via the kind registry.


class _WatchActionWireFields(BaseModel):
    """Kind-independent fields every action node carries on the wire.

    `id` is "" only on a dry-run preview (the action isn't persisted yet); a
    real read always carries it. `summary` is the server-built display
    projection; `config` (typed, per member) is the authoritative shape."""

    id: str = ""
    rank: int
    summary: WatchActionConfigSummary = Field(default_factory=WatchActionConfigSummary)
    created_at: datetime | None = None


class SemanticFilterActionWire(_WatchActionWireFields):
    kind: Literal[WatchActionKind.SEMANTIC_FILTER] = WatchActionKind.SEMANTIC_FILTER
    config: SemanticFilterConfig


class ExtractActionWire(_WatchActionWireFields):
    kind: Literal[WatchActionKind.EXTRACT] = WatchActionKind.EXTRACT
    config: ExtractConfig


class LogActionWire(_WatchActionWireFields):
    kind: Literal[WatchActionKind.LOG] = WatchActionKind.LOG
    config: LogConfig


class WebhookActionWire(_WatchActionWireFields):
    kind: Literal[WatchActionKind.WEBHOOK] = WatchActionKind.WEBHOOK
    config: WebhookConfig


# One action node on the wire: a discriminated union keyed by `kind` (a plain
# type alias, like SourceSpec, so `action.kind` / `action.config` read straight
# off the narrowed member with no wrapper).
WatchActionWire = Annotated[
    SemanticFilterActionWire | ExtractActionWire | LogActionWire | WebhookActionWire,
    Field(discriminator="kind"),
]

# The union is a type alias, not a class, so it has no `.model_validate`; this
# adapter is the single validation entry (both the server builders and the CLI
# response parsing go through it). Keys on the sibling `kind`.
watch_action_wire_adapter: TypeAdapter[WatchActionWire] = TypeAdapter(WatchActionWire)


def build_watch_action_wire(
    *,
    kind: WatchActionKind | str,
    rank: int,
    config: dict[str, Any],
    id: str = "",
    summary: WatchActionConfigSummary | None = None,
    created_at: datetime | None = None,
) -> WatchActionWire:
    """Build a WatchActionWire union member from its parts. `config` is the PURE
    kind-specific config dict (NO `kind` nested inside); `kind` is the sibling
    discriminator the union keys on. Validates through the adapter so the right
    member is selected and `config` typed to it."""
    payload: dict[str, Any] = {"id": id, "kind": kind, "rank": rank, "config": config}
    if summary is not None:
        payload["summary"] = summary
    if created_at is not None:
        payload["created_at"] = created_at
    return watch_action_wire_adapter.validate_python(payload)


class _WatchActionInputFields(BaseModel):
    """Kind-independent fields on a create / edit / add-action request.

    `id` is the STABLE identity of an existing action, carried back on a
    whole-chain edit so the server matches by id (NOT list position): matched
    actions are updated in place ; their id + run history survive, and a masked
    secret restores from that same row. Omit `id` (or leave it empty) for a
    brand-new action ; the server mints its id. A non-empty id that isn't on the
    watch is rejected. `rank` is optional on input (append when omitted); the
    server owns the dense renumber. Extra keys ignored so an edit seed's
    read-only fields drop on round-trip."""

    id: str = ""
    rank: int | None = None

    model_config = {"extra": "ignore"}


class SemanticFilterActionInput(_WatchActionInputFields):
    kind: Literal[WatchActionKind.SEMANTIC_FILTER] = WatchActionKind.SEMANTIC_FILTER
    config: SemanticFilterConfig


class ExtractActionInput(_WatchActionInputFields):
    kind: Literal[WatchActionKind.EXTRACT] = WatchActionKind.EXTRACT
    config: ExtractConfig


class LogActionInput(_WatchActionInputFields):
    kind: Literal[WatchActionKind.LOG] = WatchActionKind.LOG
    config: LogConfig


class WebhookActionInput(_WatchActionInputFields):
    kind: Literal[WatchActionKind.WEBHOOK] = WatchActionKind.WEBHOOK
    config: WebhookConfig


# One action on a create / edit / add-action request: a discriminated union
# keyed by `kind`, with `config` the pure typed kind-specific shape. A plain
# type alias (like SourceSpec) so field access needs no wrapper; the persisted
# blob stays the pure config (no `kind` nested inside).
WatchActionInput = Annotated[
    SemanticFilterActionInput | ExtractActionInput | LogActionInput | WebhookActionInput,
    Field(discriminator="kind"),
]

# Companion adapter for the write-side union (see watch_action_wire_adapter):
# the single validation entry for a `{kind, config, ...}` action request dict.
watch_action_input_adapter: TypeAdapter[WatchActionInput] = TypeAdapter(WatchActionInput)


def build_watch_action_input(
    *,
    kind: WatchActionKind | str,
    config: dict[str, Any],
    id: str = "",
    rank: int | None = None,
) -> WatchActionInput:
    """Build a WatchActionInput union member from its parts. `config` is the PURE
    kind-specific config dict (NO `kind` nested); `kind` is the sibling
    discriminator. Validates through the adapter to select the right member."""
    return watch_action_input_adapter.validate_python({"id": id, "kind": kind, "rank": rank, "config": config})
