"""Typed construction/discovery factory over the GENERATED per-kind
config models.

Hand-written and stable (~2 functions, no schema knowledge) - the
schema lives in `_generated_configs.CONFIG_BY_KIND`, regenerated from
the server. This is the *additive* construction/discovery layer: it
lets a client build a config with types and enumerate kinds/fields
WITHOUT the `core` repo. It is NEVER on the round-trip path -
`ListenerWire.data` stays opaque, so a client missing a brand-new kind
still lists/gets/edits fine; it just can't *construct* that kind until
regenerated.
"""

from __future__ import annotations

from ._gen_base import ListenerConfig
from ._generated_configs import CONFIG_BY_KIND


def available_kinds() -> list[str]:
    """Every listener kind this client knows how to construct, sorted.
    Discoverable without the server repo."""
    return sorted(CONFIG_BY_KIND)


def config_model(kind: str) -> type[ListenerConfig]:
    """The generated config model for `kind` (the type to 'jump to').
    Raises KeyError for a kind this client wasn't generated against -
    upgrade/regenerate to construct newer kinds."""
    return CONFIG_BY_KIND[kind]
