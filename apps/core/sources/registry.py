"""Connector registry. Maps kind string → Connector instance.

Values are `Connector[Any]`: each concrete connector is generic over its
own spec variant, but the registry dispatches by `kind` string so the
variant is erased here ; the call seam (`polling.py`) passes a runtime
`SourceSpec` that the kind guarantees matches the stored connector.
"""

from typing import Any

from common.models import reject_bad_plugin_kind
from sources.connectors import (
    Connector,
    FacebookGroupConnector,
    HackerNewsCommentConnector,
    HackerNewsFeedConnector,
    RedditSubRedditConnector,
    RssConnector,
    TwitterSearchConnector,
)

_REGISTRY: dict[str, Connector[Any]] = {
    FacebookGroupConnector.kind: FacebookGroupConnector(),
    RedditSubRedditConnector.kind: RedditSubRedditConnector(),
    RssConnector.kind: RssConnector(),
    HackerNewsFeedConnector.kind: HackerNewsFeedConnector(),
    HackerNewsCommentConnector.kind: HackerNewsCommentConnector(),
    TwitterSearchConnector.kind: TwitterSearchConnector(),
}

# Core kinds captured before any plugin registers; a plugin can't replace one.
_BUILTIN_KINDS = frozenset(_REGISTRY)


def get(kind: str) -> Connector[Any]:
    """Raises KeyError if the kind has no registered connector."""
    return _REGISTRY[kind]


def register(connector: Connector[Any]) -> None:
    """Register a plugin connector by its `kind`. Raises if `kind` is empty (mirrors
    the config registry) or a built-in (a plugin can't silently replace a core
    connector; pick a distinct kind)."""
    reject_bad_plugin_kind(
        connector.kind, builtin_kinds=_BUILTIN_KINDS, noun="source", owner=f"{type(connector).__name__}.kind"
    )
    existing = _REGISTRY.get(connector.kind)
    if existing is not None and type(existing) is not type(connector):
        # Fail loud on a collision (two plugins claiming the same kind); re-registering
        # the same connector class is idempotent. Silent last-wins would route polls to
        # whichever hook loaded last.
        raise ValueError(f"source kind {connector.kind!r} already has a connector ({type(existing).__name__})")
    _REGISTRY[connector.kind] = connector


def register_source(connector: Connector[Any]) -> None:
    """Register a plugin source KIND end to end in one call: the connector (by its
    `kind`) AND its `payloads` (in `payload_registry`, per (kind, PAYLOAD_KIND)).
    The connector + payload registries are an internal split; a plugin author
    shouldn't juggle both, so this registers both. Use this from a plugin register
    hook.

    Validate-then-mutate: the payload classes are checked BEFORE either registry is
    touched. The plugin loader swallows a hook's exception, so a half-registration
    (connector present, payloads absent, a realistic mistake such as a payload class
    missing its `sample()` override) would otherwise boot silently, and the kind would
    pass the write gate, poll + store items, then fail EVERY run permanently at
    hydration. `require_valid_payloads` is pure, so if it raises nothing is registered;
    `register` then applies its own built-in-kind guard before its mutation, and the
    pre-validated `payload_registry.register` can't fail after it."""
    from sources import payload_registry

    payload_registry.require_valid_payloads(connector.payloads)
    register(connector)
    # register() re-runs require_valid_payloads internally; the up-front call above is
    # the deliberate pre-check (cheap, pure) that makes this whole sequence
    # all-or-nothing. It isn't redundant: it's what lets register(connector) run first.
    payload_registry.register(connector.kind, connector.payloads)
