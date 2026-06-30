"""`magpie feed` command group.

`_apps` owns the typer apps (`feed` + the `source` / `item` sub-nouns mounted on
it); importing `_crud`, `_items`, `_lifecycle`, `_sources` registers their verbs as
a side effect. `feed_app` is what cli.py mounts.
"""

from . import _crud, _items, _lifecycle, _sources  # noqa: F401  side-effect: register verbs
from ._apps import feed_app

__all__ = ["feed_app"]
