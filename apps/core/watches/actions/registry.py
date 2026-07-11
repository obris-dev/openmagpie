"""Action-implementation registry: kind -> runnable `Action` instance.

The EXECUTION-layer registry, distinct from `watches.registry` (the
CONFIG-layer kind -> Pydantic config class). The drain looks up the impl
for a run's `action.kind` here and calls `.run(...)`. A new action kind
registers its impl here ; same shape as `engine.registry` /
`sources.registry`.
"""

from pydantic import BaseModel

from common.models import reject_bad_plugin_kind
from openmagpie_schema.watch_actions import WatchActionConfigBase

from .extract import ExtractAction
from .log import LogAction
from .protocol import Action
from .semantic_filter import SemanticFilterAction
from .webhook import WebhookAction

_REGISTRY: dict[str, Action] = {
    SemanticFilterAction.kind: SemanticFilterAction(),
    ExtractAction.kind: ExtractAction(),
    WebhookAction.kind: WebhookAction(),
    LogAction.kind: LogAction(),
}

# Core kinds captured before any plugin registers; a plugin can't replace one.
_BUILTIN_KINDS = frozenset(_REGISTRY)


def get(kind: str) -> Action:
    """The runnable Action for `kind`. Raises KeyError if no impl is
    registered (a kind that validates as config but has no executor yet ;
    the drain treats that as a permanent ERROR on the run)."""
    return _REGISTRY[kind]


def register(action: Action) -> None:
    """Register a plugin action impl by its `kind`. Raises if `kind` is empty (mirrors
    the config registry) or a built-in (a plugin can't silently replace a core
    executor; pick a distinct kind)."""
    reject_bad_plugin_kind(
        action.kind, builtin_kinds=_BUILTIN_KINDS, noun="action", owner=f"{type(action).__name__}.kind"
    )
    existing = _REGISTRY.get(action.kind)
    if existing is not None and type(existing) is not type(action):
        # Fail loud on a collision (two plugins claiming the same kind); re-registering
        # the same impl class is idempotent. Silent last-wins would route runs to
        # whichever hook loaded last.
        raise ValueError(f"action kind {action.kind!r} already has an executor ({type(existing).__name__})")
    _REGISTRY[action.kind] = action


def register_action(
    action: Action,
    config: type[WatchActionConfigBase],
    result: type[BaseModel] | None = None,
) -> None:
    """Register a plugin action KIND end to end in one call: its runnable impl AND
    its typed config class (and optionally a result schema). The execution registry
    (here) and the config registry (`watches.registry`) are an internal split; a
    plugin author shouldn't juggle both, so this registers them and rejects an
    impl/config that disagree on the kind.

    Pass `result` to make the kind's run result ENFORCED: both terminal paths (the
    instant drain and the digest flush) validate a SUCCEEDED run's result against it
    and mark the run ERRORED on a mismatch, so a consumer can rely on the result shape
    the way it relies on the config. Omit it to keep the result an unchecked blob (the
    action's own responsibility, as with the built-in kinds). Use this from a plugin
    register hook."""
    from watches import registry as config_registry  # lazy: config layer has Django-model imports

    if action.kind != config.CONFIG_KIND:
        raise ValueError(
            f"action kind mismatch: impl kind={action.kind!r} but config CONFIG_KIND={config.CONFIG_KIND!r}"
        )
    # Impl FIRST, then config: the write gate keys off the CONFIG registry, so if a
    # later step failed, an executor with no config is inert (the kind isn't accepted),
    # whereas a config with no executor would accept writes that then permanently ERROR
    # at the drain. Register the fail-safe direction. (This ordering discipline differs
    # from register_source's pre-validate-then-mutate on purpose: there the second
    # registry can raise on a bad payload, so it's pre-checked; here both steps are
    # simple dict writes that only raise on the built-in guard, so fail-safe ORDER is
    # the right tool. Both leave no half-usable kind.)
    register(action)
    config_registry.register(config)
    if result is not None:
        config_registry.register_result(action.kind, result)
