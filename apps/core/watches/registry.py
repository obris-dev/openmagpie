"""WatchAction kind -> typed config class, with the parse/validate/load
family.

The watches analogue of `feeds.registry`, same shape: a `kind -> config
class` dict, and `parse_config` is the ONE place
`get_config_class(kind).model_validate(...)` is called. `kind` is passed
explicitly (it lives on the WatchAction row / write envelope, NOT inside
the `config` blob), so the blob is the pure kind-specific shape.
"""

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

# The kinds the server accepts on a write ; the serializer/view checks
# membership for a clean "unknown kind" 400 before handing off.
KNOWN_KINDS: frozenset[str] = frozenset(_REGISTRY)


def get_config_class(kind: str) -> type[WatchActionConfigBase]:
    """Raises KeyError if `kind` has no registered config class."""
    return _REGISTRY[kind]


def parse_config(kind: str, data: dict) -> WatchActionConfigBase:
    """kind + raw `config` dict -> typed config, SHAPE ONLY (no policy).
    The ONE place `get_config_class(kind).model_validate(...)` is called.

    Raises KeyError on an unknown kind (caller gates with KNOWN_KINDS
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
