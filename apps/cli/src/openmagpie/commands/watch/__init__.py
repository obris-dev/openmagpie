"""`magpie watch` command group.

`_apps` owns the typer apps; importing `_actions`, `_crud`, `_lifecycle`
registers their verbs onto those apps as a side effect. `watch_app` is what
cli.py mounts.
"""

from . import _actions, _crud, _lifecycle  # noqa: F401  side-effect: register verbs
from ._apps import watch_app

__all__ = ["watch_app"]
