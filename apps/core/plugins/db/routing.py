"""Maps a Django app_label to the database alias its models live in.

Part of forkable extensibility: a plugin's register hook calls
`route_app(app_label, alias)` so that app's tables are stored in a separate
database (see `plugins.db.routers` and the extra-DB settings), keeping core's
schema and migrations untouched. Empty by default, so every app stays on the
`default` database.
"""

from __future__ import annotations

# Safe at module level: routing.py is imported lazily (via the router) only after
# Django settings are configured, and `settings` is the lazy proxy. Hoisted out of
# db_for_app because that runs on every query.
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

_APP_DB: dict[str, str] = {}


def route_app(app_label: str, db_alias: str) -> None:
    """Route an app's models to `db_alias`. Call from a plugin register hook.

    Fails loudly (ImproperlyConfigured) if `db_alias` is not a defined database,
    so a typo surfaces at registration time rather than as a ConnectionDoesNotExist
    on the first query. Mirrors the config-file path's alias check in
    `plugins.db.config`.
    """
    if db_alias not in settings.DATABASES:
        raise ImproperlyConfigured(
            f"route_app({app_label!r}) targets database alias {db_alias!r}, "
            f"which is not defined in DATABASES (known: {sorted(settings.DATABASES)})"
        )
    _APP_DB[app_label] = db_alias


def db_for_app(app_label: str) -> str | None:
    """The alias `app_label` is routed to, or None (meaning: use `default`).

    Runtime registrations (`route_app`, e.g. from a plugin hook) win; otherwise
    fall back to the config-file map in `settings.PLUGIN_DB_ROUTING`.
    """
    if app_label in _APP_DB:
        return _APP_DB[app_label]
    return settings.PLUGIN_DB_ROUTING.get(app_label)
