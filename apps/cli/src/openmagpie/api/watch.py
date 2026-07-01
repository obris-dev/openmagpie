"""Watches API resource client.

Wraps the `/v1/watches` endpoints. ALL shapes live ONCE in the shared
`openmagpie_schema.watch` package (the server is the authority) and are
imported verbatim here — including the write envelope `WatchInput`, which
the CLI constructs. No CLI-side copy. Mirrors `api/feed.py`.
"""

from __future__ import annotations

import builtins
from typing import Any

from openmagpie_schema.watch import (
    WatchActionMutationResponse,
    WatchActionWire,
    WatchInput,
    WatchListResponse,
    WatchMutationResponse,
    WatchView,
    WatchWire,
    build_watch_action_input,
    watch_action_wire_adapter,
)

from .. import routes
from ..http import MagpieClient

__all__ = [
    "WatchActionMutationResponse",
    "WatchApi",
    "WatchInput",
    "WatchListResponse",
    "WatchMutationResponse",
    "WatchView",
    "WatchWire",
]


def _action_body(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """The single-action add/edit request body, built from the shared typed
    `WatchActionInput` envelope rather than hand-rolled - one definition of the
    `{kind, config}` wire shape. `id` is minted server-side; `rank` (add only) is
    layered on by the caller."""
    # Exclude id (server-minted, never sent) + rank (add re-layers it from the
    # caller below; edit never positions) - so the dump is exactly {kind, config}.
    return build_watch_action_input(kind=kind, config=config).model_dump(mode="json", exclude={"id", "rank"})


class WatchApi:
    """Resource client for `/v1/watches`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any], *, dry_run: bool = False) -> WatchMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.watches.collection, json_body=body, params=params)
        return WatchMutationResponse.model_validate(raw)

    def list(self, *, after: str | None = None, limit: int | None = None) -> WatchListResponse:
        """One page of watches (cursor-paginated, newest-first by ULID pk).
        `after` = id of the last watch from the previous page; omit on the
        first call. `next_cursor` is None when there are no more rows."""
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        raw = self._http.get(routes.watches.collection, params=params or None)
        return WatchListResponse.model_validate(raw)

    def get(self, watch_id: str) -> WatchView:
        """GET one watch (account-scoped). Carries the initial path's
        ordered action chain."""
        raw = self._http.get(routes.watches.detail(watch_id))
        return WatchView.model_validate(raw)

    def update(self, watch_id: str, body: dict[str, Any], *, dry_run: bool = False) -> WatchMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.watches.detail(watch_id), json_body=body, params=params)
        return WatchMutationResponse.model_validate(raw)

    def set_active(self, watch_id: str, *, is_active: bool) -> WatchView:
        """PATCH the active flag only (pause/resume): the trigger pass stops/starts
        running this watch. No chain replace, unlike update()."""
        raw = self._http.patch(routes.watches.detail(watch_id), json_body={"is_active": is_active})
        return WatchView.model_validate(raw)

    def delete(self, watch_id: str) -> None:
        self._http.delete(routes.watches.detail(watch_id))

    # ── Actions sub-resource (the chain) ────────────────────────────────

    def list_actions(self, watch_id: str) -> builtins.list[WatchActionWire]:
        raw = self._http.get(routes.watches.actions(watch_id))
        items = (raw or {}).get("items") or []
        return [watch_action_wire_adapter.validate_python(it) for it in items]

    def get_action(self, action_id: str) -> WatchActionWire:
        """GET one action's definition (kind + redacted config + summary) by its
        own id; the watch/chain is resolved server-side, not in the path."""
        raw = self._http.get(routes.actions.detail(action_id))
        return watch_action_wire_adapter.validate_python(raw)

    def add_action(
        self, watch_id: str, kind: str, config: dict[str, Any], *, rank: int | None = None, dry_run: bool = False
    ) -> WatchActionMutationResponse:
        body = _action_body(kind, config)
        if rank is not None:
            body["rank"] = rank
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.watches.actions(watch_id), json_body=body, params=params)
        return WatchActionMutationResponse.model_validate(raw)

    def edit_action(
        self, action_id: str, kind: str, config: dict[str, Any], *, dry_run: bool = False
    ) -> WatchActionMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.actions.detail(action_id), json_body=_action_body(kind, config), params=params)
        return WatchActionMutationResponse.model_validate(raw)

    def delete_action(self, action_id: str) -> None:
        self._http.delete(routes.actions.detail(action_id))
