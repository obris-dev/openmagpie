"""Tiny env-parsing helpers.

Settings boolean reads like
    AUTH_COOKIE_SECURE = os.environ.get("X", "false").lower() == "true"
are whitespace-fragile, a trailing newline or leading space from a
secrets manager flips the meaning silently. `env_bool` strips before
comparing.
"""

from __future__ import annotations

import os


def env_bool(name: str, default: str = "false") -> bool:
    """Return True iff the env var (after strip + casefold) equals 'true'.

    Anything else (including unset -> falls back to `default`, then
    'false' / '0' / 'no' / empty) is False. Whitespace and case
    differences don't change the result.
    """
    return os.environ.get(name, default).strip().lower() == "true"


def split_csv(value: str) -> list[str]:
    """Comma-separated string -> list of stripped, non-empty items.

    The canonical parse for our comma-separated env vars: a stray space or
    trailing newline from a secrets manager shouldn't create phantom '' entries
    or leave surrounding whitespace on a value.
    """
    return [item.strip() for item in value.split(",") if item.strip()]


def env_list(name: str, default: str = "") -> list[str]:
    """Comma-separated env var -> list of stripped, non-empty items (`split_csv`
    of the value). Unset -> `default` -> []."""
    return split_csv(os.environ.get(name, default))
