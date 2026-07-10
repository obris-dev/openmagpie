"""Load extra database connections + app routing from a JSON config file.

Part of forkable extensibility: a fork points OPENMAGPIE_DB_CONFIG at a JSON
file so its apps' tables live in whatever database it chooses, without editing
core's DATABASES or entering core's migrations. Shape:

    {
      "databases": {"<alias>": {"NAME","USER","PASSWORD","HOST","PORT",["ENGINE"]}},
      "routing":   {"<app_label>": "<alias>"}
    }

The routing key is the Django **app label** (e.g. `myfork_app`), not a dotted
path: `PluginAppRouter` matches on `model._meta.app_label`, so a dotted-path key
would silently never match and the app's tables would land in core's database.

Mount the file as a secret, since it may carry DB credentials. Errors loudly (a
misconfig must fail at boot, not silently mis-route or clobber a connection).

Import-light on purpose: this is imported at settings-eval time, so it must NOT
touch the app registry or import models (that would break settings loading).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS


def load_db_config(path: str, databases: dict[str, Any], *, conn_max_age: int) -> dict[str, str]:
    """Merge the file's `databases` into `databases` (in place) and return its
    `routing` map (app_label -> alias). Raises `ImproperlyConfigured`, always
    naming OPENMAGPIE_DB_CONFIG, on: an unreadable or non-UTF-8 path, malformed
    JSON, a non-object top level (or non-object `databases` / connection entry /
    `routing`), a conflicting alias (would clobber an existing connection), a
    database missing NAME or with a non-integer CONN_MAX_AGE / bad PORT, or a
    route to an undefined alias."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG file {path!r} could not be read ({exc})") from exc
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG file {path!r} is not valid JSON ({exc})") from exc
    if not isinstance(config, dict):
        raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG file {path!r} must be a JSON object")

    # Inherit connection defaults from the already-resolved core connection so a
    # plugin DB on the same server needs only NAME, and the Postgres defaults
    # aren't re-hardcoded here (they'd drift from conf.settings.base). PASSWORD is
    # never inherited: a plugin DB the operator didn't configure must not silently
    # reuse core's credentials.
    default_conn = databases.get(DEFAULT_DB_ALIAS, {})
    config_databases = config.get("databases", {})
    if not isinstance(config_databases, dict):
        raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG file {path!r} 'databases' must be a JSON object")
    for alias, conn in config_databases.items():
        if alias in databases:
            raise ImproperlyConfigured(
                f"OPENMAGPIE_DB_CONFIG database alias {alias!r} conflicts with an existing connection"
            )
        if not isinstance(conn, dict):
            raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG database {alias!r} must be a JSON object")
        # Reject falsy NAME (missing / null / ""): a None NAME would otherwise
        # land in DATABASES and fail cryptically on first connect.
        if not conn.get("NAME"):
            raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG database {alias!r} is missing the required 'NAME'")
        raw_max_age = conn.get("CONN_MAX_AGE", conn_max_age)
        # Reject bool (a subclass of int) and float (int() would silently truncate
        # 1.9 -> 1); accept an int or an int-parseable string.
        if isinstance(raw_max_age, bool | float):
            raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG database {alias!r} has a non-integer 'CONN_MAX_AGE'")
        try:
            resolved_max_age = int(raw_max_age)
        except (TypeError, ValueError):
            raise ImproperlyConfigured(
                f"OPENMAGPIE_DB_CONFIG database {alias!r} has a non-integer 'CONN_MAX_AGE'"
            ) from None
        port = conn.get("PORT", default_conn.get("PORT"))
        # Reject bool explicitly (a subclass of int, so `true` would pass the
        # int check and become the string "True").
        if isinstance(port, bool) or (port is not None and not isinstance(port, str | int)):
            raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG database {alias!r} has an invalid 'PORT'")
        databases[alias] = {
            "ENGINE": conn.get("ENGINE", default_conn.get("ENGINE")),
            "NAME": conn["NAME"],
            "USER": conn.get("USER", default_conn.get("USER")),
            "PASSWORD": conn.get("PASSWORD", ""),
            "HOST": conn.get("HOST", default_conn.get("HOST")),
            "PORT": "" if port is None else str(port),
            "CONN_MAX_AGE": resolved_max_age,
        }

    config_routing = config.get("routing", {})
    if not isinstance(config_routing, dict):
        raise ImproperlyConfigured(f"OPENMAGPIE_DB_CONFIG file {path!r} 'routing' must be a JSON object")
    routing: dict[str, str] = dict(config_routing)
    for app_label, alias in routing.items():
        if alias not in databases:
            raise ImproperlyConfigured(
                f"OPENMAGPIE_DB_CONFIG routes app {app_label!r} to unknown database alias {alias!r}"
            )
    return routing
