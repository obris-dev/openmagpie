"""Action-node wire + input shapes: the per-kind discriminated unions.

Split from the watch package's envelopes so each module stays under the line
cap and holds one concern. `config` is the PURE typed kind-specific shape (NO
`kind` nested inside); `kind` is the sibling discriminator the union keys on.
The server + the CLI + the web all generate from these, so `switch (action.kind)`
narrows `config` to its exact type.
"""

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field, TypeAdapter, field_validator

from .._unions import _PLUGIN_MEMBER_LOC_NAMES, KIND_MAX_LENGTH, builtin_union_kinds, reject_builtin_kind
from ..watch_actions import (
    ExtractConfig,
    LogConfig,
    SemanticFilterConfig,
    WatchActionConfigSummary,
    WebhookConfig,
)
from ..watch_enums import BUILTIN_ACTION_KINDS, WatchActionKind

# Built-in action kinds (the shared exported set); the plugin fallback members reject
# these so a malformed built-in config can't be absorbed as a raw blob.
_BUILTIN_KINDS = BUILTIN_ACTION_KINDS

# An action node is a discriminated union keyed by `kind`. `config` is the PURE
# kind-specific typed shape (NO `kind` nested inside; `kind` is the sibling
# discriminator the union keys on). This is the single typed source both the CLI
# and the web generate from, so `switch (action.kind)` narrows `config` to its
# exact type. The server validates + builds these via the kind registry.


class WatchActionWireFields(BaseModel):
    """Kind-independent fields every action node carries on the wire.

    `id` is "" only on a dry-run preview (the action isn't persisted yet); a
    real read always carries it. `summary` is the server-built display
    projection; `config` (typed, per member) is the authoritative shape."""

    id: str = ""
    rank: int
    summary: WatchActionConfigSummary = Field(default_factory=WatchActionConfigSummary)
    created_at: datetime | None = None


# The WIRE config is Optional ONLY as a corrupt-at-rest degrade: the server
# validates config on write, so a real read always has one, but a config that no
# longer types (a manual DB edit, a tightened schema) degrades to None rather than
# 500 the list, mirroring the run wire's `result: <Typed> | None`. The INPUT
# members below keep config REQUIRED (you can't author an action without one).
class SemanticFilterActionWire(WatchActionWireFields):
    kind: Literal[WatchActionKind.SEMANTIC_FILTER] = WatchActionKind.SEMANTIC_FILTER
    config: SemanticFilterConfig | None = None


class ExtractActionWire(WatchActionWireFields):
    kind: Literal[WatchActionKind.EXTRACT] = WatchActionKind.EXTRACT
    config: ExtractConfig | None = None


class LogActionWire(WatchActionWireFields):
    kind: Literal[WatchActionKind.LOG] = WatchActionKind.LOG
    config: LogConfig | None = None


class WebhookActionWire(WatchActionWireFields):
    kind: Literal[WatchActionKind.WEBHOOK] = WatchActionKind.WEBHOOK
    config: WebhookConfig | None = None


class PluginActionWire(WatchActionWireFields):
    """Fallback wire member for a plugin (non-built-in) action kind. `kind` is any
    non-built-in string; `config` is an untyped blob (a fork's typed config schema
    lives in the fork's own contract, and its web/CLI narrow on `kind`). Selected
    only when no built-in discriminator matches (see the left-to-right union)."""

    kind: str = Field(min_length=1, max_length=KIND_MAX_LENGTH)
    config: dict[str, Any] | None = None

    @field_validator("kind")
    @classmethod
    def _not_builtin(cls, v: str) -> str:
        return reject_builtin_kind(v, _BUILTIN_KINDS)


# One action node on the wire, keyed by `kind`: the four built-ins as a
# discriminated union, then a left-to-right fallthrough to the plugin member for
# any other kind. A built-in tag always takes its typed branch; a built-in whose
# config is malformed fails BOTH branches (the plugin member rejects built-in
# kinds), so the server's per-row catch still degrades it to config=None rather
# than the fallback silently absorbing a corrupt built-in config as a raw dict.
_BuiltinWatchActionWire = Annotated[
    SemanticFilterActionWire | ExtractActionWire | LogActionWire | WebhookActionWire,
    Field(discriminator="kind"),
]
WatchActionWire = Annotated[_BuiltinWatchActionWire | PluginActionWire, Field(union_mode="left_to_right")]

# The union is a type alias, not a class, so it has no `.model_validate`; this
# adapter is the single validation entry (both the server builders and the CLI
# response parsing go through it). Keys on the sibling `kind`.
watch_action_wire_adapter: TypeAdapter[WatchActionWire] = TypeAdapter(WatchActionWire)


def build_watch_action_wire(
    *,
    kind: WatchActionKind | str,
    rank: int,
    config: dict[str, Any] | None,
    id: str = "",
    summary: WatchActionConfigSummary | None = None,
    created_at: datetime | None = None,
) -> WatchActionWire:
    """Build a WatchActionWire union member from its parts. `config` is the PURE
    kind-specific config dict (NO `kind` nested inside); `kind` is the sibling
    discriminator the union keys on. Validates through the adapter so the right
    member is selected and `config` typed to it. `config=None` is the
    corrupt-at-rest degrade (the server passes None when the stored config no
    longer types), which the wire member's Optional config accepts."""
    payload: dict[str, Any] = {"id": id, "kind": kind, "rank": rank, "config": config}
    if summary is not None:
        payload["summary"] = summary
    if created_at is not None:
        payload["created_at"] = created_at
    return watch_action_wire_adapter.validate_python(payload)


class WatchActionInputFields(BaseModel):
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


class SemanticFilterActionInput(WatchActionInputFields):
    kind: Literal[WatchActionKind.SEMANTIC_FILTER] = WatchActionKind.SEMANTIC_FILTER
    config: SemanticFilterConfig


class ExtractActionInput(WatchActionInputFields):
    kind: Literal[WatchActionKind.EXTRACT] = WatchActionKind.EXTRACT
    config: ExtractConfig


class LogActionInput(WatchActionInputFields):
    kind: Literal[WatchActionKind.LOG] = WatchActionKind.LOG
    config: LogConfig


class WebhookActionInput(WatchActionInputFields):
    kind: Literal[WatchActionKind.WEBHOOK] = WatchActionKind.WEBHOOK
    config: WebhookConfig


class PluginConfigBlob(BaseModel):
    """The write-side config for a plugin action kind: an open blob. The server
    re-validates it against the kind's registered Pydantic config (watches.registry),
    so the wire type stays permissive; `extra="allow"` keeps every submitted key
    through `model_dump(mode="json")`."""

    model_config = {"extra": "allow"}


class PluginActionInput(WatchActionInputFields):
    """Fallback input member for a plugin (non-built-in) action kind. Mirrors
    PluginActionWire on the write path; `config` is required (you can't author an
    action without one) but open (validated server-side by the kind's registry)."""

    kind: str = Field(min_length=1, max_length=KIND_MAX_LENGTH)
    config: PluginConfigBlob

    @field_validator("kind")
    @classmethod
    def _not_builtin(cls, v: str) -> str:
        return reject_builtin_kind(v, _BUILTIN_KINDS)


# One action on a create / edit / add-action request, keyed by `kind`: the four
# built-ins as a discriminated union, then a left-to-right fallthrough to the
# plugin member (same discipline as WatchActionWire). The persisted blob stays the
# pure config (no `kind` nested inside).
_BuiltinWatchActionInput = Annotated[
    SemanticFilterActionInput | ExtractActionInput | LogActionInput | WebhookActionInput,
    Field(discriminator="kind"),
]
WatchActionInput = Annotated[_BuiltinWatchActionInput | PluginActionInput, Field(union_mode="left_to_right")]

# Import-time parity guard (the enum-side analogue of configs.py's source guard): the
# built-in members of BOTH unions must be exactly the WatchActionKind enum, so the
# enum-derived _BUILTIN_KINDS the plugin fallback rejects can't drift from what the
# unions actually discriminate. The activity-failsafe test also pins this; the raise
# makes it loud at import for a fork.
for _name, _union in (("WatchActionWire", _BuiltinWatchActionWire), ("WatchActionInput", _BuiltinWatchActionInput)):
    if builtin_union_kinds(_union) != _BUILTIN_KINDS:
        raise RuntimeError(
            f"{_name} built-in members {sorted(builtin_union_kinds(_union))} != "
            f"WatchActionKind {sorted(_BUILTIN_KINDS)}"
        )

# Cross-pin each built-in member's `kind` Literal to its config class's CONFIG_KIND (the
# action analogue of configs.py's SOURCE_KIND pin). The discriminator Literal, the enum,
# and the config's own declared CONFIG_KIND are three INDEPENDENT declarations; without
# this a member could validate as one kind while its config's registration key claims
# another (the config registry keys on CONFIG_KIND). Core's BuiltinActionKindInvariantTests
# catches this drift downstream, but pinning it here makes the package self-defending,
# matching the source side. Derive the members from the union (like configs.py's pin),
# NOT a hand-written tuple: a fifth built-in added to the union would otherwise pass the
# kind-set guard above but silently skip this pin, half-recreating the drift it closes.
for _member in get_args(get_args(_BuiltinWatchActionInput)[0]):
    _literal = get_args(_member.model_fields["kind"].annotation)
    _config_kind = getattr(_member.model_fields["config"].annotation, "CONFIG_KIND", None)
    # Normalize each Literal via `.value` (guarded), exactly like builtin_union_kinds, so
    # the pin doesn't depend on WatchActionKind being a StrEnum: `str()` would yield
    # "WatchActionKind.X" for a plain Enum and raise a false import-time RuntimeError for
    # every consumer. The set compares the "exactly one, and it matches" check in one go.
    _kinds = {v.value if isinstance(v, Enum) else v for v in _literal}
    if _kinds != {_config_kind}:
        raise RuntimeError(
            f"{_member.__name__}: kind Literal {_literal} must match its config's CONFIG_KIND {_config_kind!r}; "
            f"the discriminator and the config registration key would otherwise diverge"
        )

# Pin the fallback member names against the hand-maintained set in `_unions` (which sits
# below this module, so it can't reference the classes): a rename here that isn't
# mirrored there would silently turn off clean_union_errors' loc-stripping.
for _member in (PluginActionWire, PluginActionInput):
    if _member.__name__ not in _PLUGIN_MEMBER_LOC_NAMES:
        raise RuntimeError(f"{_member.__name__} missing from _unions._PLUGIN_MEMBER_LOC_NAMES")

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
