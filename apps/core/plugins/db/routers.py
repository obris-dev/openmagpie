"""Database router for forkable extensibility.

Routes a routed app's models (see `plugins.db.routing`) to its declared
database; every other app stays on `default`. Reads and writes for a routed
app go to its alias; its migrations run only there, and no other app's
migrations touch it, so a fork's tables live in a separate database and never
enter core's schema or migration history.

A harmless no-op until a plugin routes an app: with nothing routed, every app
resolves to `default`, so core behaviour is unchanged.

Routing is keyed by `model._meta.app_label` (the Django app LABEL, e.g.
`myfork_app`), so a routing/`route_app` key must be that label, not a dotted
path. A dotted-path key silently never matches and the app's tables land in
`default` with no error.
"""

from __future__ import annotations

from typing import Any

from django.db import DEFAULT_DB_ALIAS

from plugins.db.routing import db_for_app


class PluginAppRouter:
    def db_for_read(self, model: Any, **hints: Any) -> str | None:
        return db_for_app(model._meta.app_label)

    def db_for_write(self, model: Any, **hints: Any) -> str | None:
        return db_for_app(model._meta.app_label)

    def allow_relation(self, obj1: Any, obj2: Any, **hints: Any) -> bool | None:
        # No opinion; cross-database relations aren't used (models on a routed
        # DB reference core rows by id, not by ForeignKey).
        return None

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool | None:
        target = db_for_app(app_label)
        # A routed app migrates only on its own DB; every other app only on `default`.
        return db == (target if target is not None else DEFAULT_DB_ALIAS)
