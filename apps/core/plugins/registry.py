"""Generic plugin registry: a `kind` string -> value map.

OpenMagpie has four hand-rolled registries of this shape
(`watches.actions.registry`, `watches.registry`, `sources.registry`,
`engine.registry`), each a module-level dict keyed by a `kind` string, though
their accessors differ (`watches.registry`, for instance, exposes
`get_config_class` / `parse_config` and a frozen `KNOWN_KINDS`, not
`get`/`register`). `Registry` is the shared primitive for NEW plugin categories
(e.g. a future datastore); the existing four are intentionally left as-is.

`get` raises `KeyError` on an unknown kind, the same contract the existing
registries use (callers already treat that as "no executor" / a clean 400).
"""

from __future__ import annotations


class Registry[T]:
    """A `kind` -> value map for one plugin category."""

    def __init__(self, category: str) -> None:
        self.category = category
        self._items: dict[str, T] = {}

    def register(self, kind: str, value: T) -> T:
        """Register (or replace) the value for `kind`; returns it so it can wrap a definition."""
        self._items[kind] = value
        return value

    def get(self, kind: str) -> T:
        """Raises KeyError if `kind` has no registered value."""
        return self._items[kind]

    def known(self, kind: str) -> bool:
        return kind in self._items

    def kinds(self) -> list[str]:
        return sorted(self._items)
