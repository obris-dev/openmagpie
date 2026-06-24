"""Shared DRF query-param parsing for the /v1 CRUD views.

`dry_run` and `?limit=` are parsed identically by every list/mutation
endpoint, so the logic lives here once instead of being copied per app.
"""

from __future__ import annotations

from rest_framework.request import Request

_TRUTHY = {"1", "true", "yes", "on"}

# List-endpoint page-size bounds. A request may ask for fewer; anything
# above the max is clamped (a client can't force an unbounded page).
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def is_truthy(value: str | None) -> bool:
    """True for the usual affirmative query-string spellings. Used for
    `?dry_run=...`. Note this is for QUERY STRINGS (always str|None) ; a
    JSON-body bool should be type-checked directly, not run through here."""
    return value is not None and value.strip().lower() in _TRUTHY


def wants_dry_run(request: Request) -> bool:
    """True when the request asks for a validate-only dry run (`?dry_run=true`).
    The one home for the dry-run query-param name + truthy parsing, shared by
    every create/edit endpoint's preview path so the param name can't drift."""
    return is_truthy(request.query_params.get("dry_run"))


def parse_limit(request: Request, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    """Read `?limit=`, clamped to [1, maximum]; falls back to `default`
    when absent or unparseable."""
    raw = request.query_params.get("limit")
    if raw is None:
        return default
    try:
        return max(1, min(maximum, int(raw)))
    except ValueError:
        return default
