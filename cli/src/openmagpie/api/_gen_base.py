"""Hand-written, stable base for the GENERATED per-kind config models.

This is the discoverability anchor: every generated `*ListenerConfig`
inherits `ListenerConfig`, so "go to definition" on the base lists
every kind, and tooling has one type to hang off. It is deliberately
EMPTY and stable - it carries no schema knowledge (that lives in the
generated subclasses, sourced from the server), so it is not the
hand-maintained-mirror drift trap. datamodel-code-generator points its
`--base-class` here.
"""

from __future__ import annotations

from pydantic import BaseModel


class ListenerConfig(BaseModel):
    """Common base for every generated per-kind config. Discoverability
    only; no fields. See `CONFIG_BY_KIND` in the generated module and
    the `listener_config` factory."""
