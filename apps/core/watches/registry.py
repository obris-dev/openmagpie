"""WatchAction kind -> typed config class, with the parse/validate/load
family.

The watches analogue of `feeds.registry`, same shape: a `kind -> config
class` dict, and `parse_config` is the ONE place
`get_config_class(kind).model_validate(...)` is called. `kind` is passed
explicitly (it lives on the WatchAction row / write envelope, NOT inside
the `config` blob), so the blob is the pure kind-specific shape.
"""

from typing import Any

from pydantic import BaseModel

from common.models import reject_bad_plugin_kind
from openmagpie_schema.watch_actions import (
    ExtractConfig,
    LogConfig,
    SemanticFilterConfig,
    WatchActionConfigBase,
    WebhookConfig,
)
from watches.models import WatchAction
from watches.policy import PolicyError, enforce_action_policy

_REGISTRY: dict[str, type[WatchActionConfigBase]] = {
    SemanticFilterConfig.CONFIG_KIND: SemanticFilterConfig,
    ExtractConfig.CONFIG_KIND: ExtractConfig,
    WebhookConfig.CONFIG_KIND: WebhookConfig,
    LogConfig.CONFIG_KIND: LogConfig,
}

# The core kinds, captured before any plugin registers. A plugin may NOT replace
# one (that would silently reshape a built-in's validation); registering one raises.
_BUILTIN_KINDS = frozenset(_REGISTRY)

# Optional per-kind result schema. A kind registered via
# `watches.actions.registry.register_action(..., result=...)` lands here, and both
# terminal paths (the instant drain and the digest flush) then validate a SUCCEEDED
# run's result against it (so a consumer can rely on the shape). Empty for built-ins
# and result-less plugins -> no enforcement, preserving the "result is the action's
# responsibility" default.
_RESULT_REGISTRY: dict[str, type[BaseModel]] = {}


def register_result(kind: str, result_cls: type[BaseModel]) -> None:
    """Register the result schema for `kind` (used by `register_action`). Refuses a
    built-in kind: a direct call with e.g. "log" would start ERRORING built-in runs
    whose result doesn't match the imposed schema (unreachable via `register_action`,
    which rejects a built-in kind up front, but this is public)."""
    if kind in _BUILTIN_KINDS:
        raise ValueError(f"{kind!r} is a built-in action kind; its result shape is fixed and can't be plugin-enforced")
    _RESULT_REGISTRY[kind] = result_cls


def enforce_result(kind: str, result: dict[str, Any]) -> None:
    """Validate a run's `result` against `kind`'s registered result schema, if one
    exists. Raises PydanticValidationError on a mismatch; a no-op for a kind with no
    registered result schema (so built-ins and result-less kinds are unaffected)."""
    result_cls = _RESULT_REGISTRY.get(kind)
    if result_cls is not None:
        result_cls.model_validate(result)


def register(config_cls: type[WatchActionConfigBase]) -> None:
    """Register the config class for its CONFIG_KIND.

    A plugin hook (see plugins/README.md) calls this at startup to add a new
    action kind, paired with `watches.actions.registry.register(impl)`. Raises if
    CONFIG_KIND is empty, or names a built-in kind (a plugin can't silently reshape
    a core default; pick a distinct kind). Returns None, like the impl + connector
    register() functions."""
    kind = config_cls.CONFIG_KIND
    reject_bad_plugin_kind(
        kind, builtin_kinds=_BUILTIN_KINDS, noun="action", owner=f"{config_cls.__name__}.CONFIG_KIND"
    )
    existing = _REGISTRY.get(kind)
    if existing is not None and existing is not config_cls:
        # Fail loud on a genuine collision (two plugins claiming the same kind); a
        # re-register of the identical class is idempotent. Silent last-wins would route
        # writes to whichever hook loaded last.
        raise ValueError(f"action kind {kind!r} is already registered by {existing.__name__}")
    _REGISTRY[kind] = config_cls


def known_kinds() -> frozenset[str]:
    """The kinds the server accepts on a write ; gates check membership for a
    clean "unknown kind" 400 before handing off. A FUNCTION, not a constant: a
    plugin registers its kind during app-ready (startup), so a value captured at
    an importer's import time would miss it. Read it live at request time."""
    return frozenset(_REGISTRY)


def get_config_class(kind: str) -> type[WatchActionConfigBase]:
    """Raises KeyError if `kind` has no registered config class."""
    return _REGISTRY[kind]


def parse_config(kind: str, data: dict) -> WatchActionConfigBase:
    """kind + raw `config` dict -> typed config, SHAPE ONLY (no policy).
    The ONE place `get_config_class(kind).model_validate(...)` is called.

    Raises KeyError on an unknown kind (caller gates with known_kinds()
    first) or PydanticValidationError on a shape violation."""
    return get_config_class(kind).model_validate(data)


def validate_config(kind: str, data: dict) -> WatchActionConfigBase:
    """Untrusted input -> policy-safe typed config: parse_config (shape) +
    enforce_action_policy fused so a callsite can't forget the policy
    half. For WRITES where the returned object is what persists.

    Raises KeyError (unknown kind), PydanticValidationError (shape), or
    PolicyError (policy)."""
    return enforce_action_policy(parse_config(kind, data))


def load_config(action: WatchAction) -> WatchActionConfigBase:
    """At-rest action -> typed config, shape only (stored data is already
    normalized, so no policy). `kind` comes from the row's column."""
    return parse_config(str(action.kind), action.config or {})


def merge_config(kind: str, data: dict, prior: WatchActionConfigBase | None) -> WatchActionConfigBase:
    """Edit-path validate: parse (shape) -> merge_preserving(prior) ->
    policy. `prior` is the at-rest config of the action being replaced, or
    None when there's nothing to carry forward (kind changed, or a fresh
    action). merge_preserving restores edit-round-trip state the submitted
    config must not reset (a redacted secret the operator left masked) ;
    today's semantic_filter has none and returns self, but the contract is
    wired so secret-bearing kinds (webhook/log) work without touching this.

    Mirrors `feeds.FeedService.build_update`. A merge refusal surfaces as
    PolicyError (-> 400), never a 500. Raises KeyError (unknown kind),
    PydanticValidationError (shape), or PolicyError (policy / merge)."""
    submitted = parse_config(kind, data)
    if prior is not None:
        try:
            submitted = submitted.merge_preserving(prior)
        except ValueError as exc:
            raise PolicyError(str(exc)) from exc
    return enforce_action_policy(submitted)
