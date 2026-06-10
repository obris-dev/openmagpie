"""Shared query-param helper for the cursor-paginated observability lists."""

from __future__ import annotations


def list_params(
    *, state: str | None = None, after: str | None = None, limit: int | None = None, window: str | None = None
) -> dict[str, str] | None:
    """The cursor-list query params shared by the activity / delivery list
    endpoints, dropping the unset ones. None when empty so httpx sends none."""
    params: dict[str, str] = {}
    if state:
        params["state"] = state
    if after:
        params["after"] = after
    if limit is not None:
        params["limit"] = str(limit)
    if window:
        params["window"] = window
    return params or None
