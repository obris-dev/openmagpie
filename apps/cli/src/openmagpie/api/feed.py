"""Feeds API resource client.

Wraps the `/v1/feeds` endpoints. Both the response models and the `FeedInput`
request envelope live ONCE in the shared `openmagpie_schema.feed` package
(mirroring `WatchInput`); this module imports them verbatim and adds the
resource client.
"""

from __future__ import annotations

from typing import Any

from openmagpie_schema.feed import (
    FeedInput,
    FeedItemListResponse,
    FeedItemWire,
    FeedListResponse,
    FeedMutationResponse,
    FeedView,
    FeedWire,
    SourceSetResult,
    SourceWire,
)

from .. import routes
from ..http import MagpieClient

__all__ = [
    "FeedApi",
    "FeedInput",
    "FeedItemListResponse",
    "FeedItemWire",
    "FeedListResponse",
    "FeedMutationResponse",
    "FeedView",
    "FeedWire",
    "SourceSetResult",
    "SourceWire",
]


class FeedApi:
    """Resource client for `/v1/feeds`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any], *, dry_run: bool = False) -> FeedMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.feeds.collection, json_body=body, params=params)
        return FeedMutationResponse.model_validate(raw)

    def list(self, *, after: str | None = None, limit: int | None = None) -> FeedListResponse:
        """One page of feeds (cursor-paginated, newest-first by ULID pk).
        `after` = id of the last feed from the previous page; omit on first
        call. The returned `next_cursor` is None when there are no more rows.
        """
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        raw = self._http.get(routes.feeds.collection, params=params or None)
        return FeedListResponse.model_validate(raw)

    def get(self, feed_id: str) -> FeedView:
        """GET one feed's CONFIG detail (account-scoped): the kind-independent
        envelope + display `summary` + its current source set. The item log is a
        separate read (`feed item list` / GET /v1/feeds/<id>/items); this view is
        the feed's configuration, not its items."""
        raw = self._http.get(routes.feeds.detail(feed_id))
        return FeedView.model_validate(raw)

    def update(self, feed_id: str, body: dict[str, Any], *, dry_run: bool = False) -> FeedMutationResponse:
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.feeds.detail(feed_id), json_body=body, params=params)
        return FeedMutationResponse.model_validate(raw)

    def set_active(self, feed_id: str, *, is_active: bool) -> FeedView:
        """PATCH the active flag only (pause/resume): the server stops/starts polling
        this feed's sources. No config replace, unlike update()."""
        raw = self._http.patch(routes.feeds.detail(feed_id), json_body={"is_active": is_active})
        return FeedView.model_validate(raw)

    def delete(self, feed_id: str) -> None:
        self._http.delete(routes.feeds.detail(feed_id))

    # ── Items sub-resource (read-only) ─────────────────────────────────

    def list_items(self, feed_id: str, *, after: str | None = None, limit: int | None = None) -> FeedItemListResponse:
        """One page of the feed's items (cursor-paginated, newest-first by ULID
        pk). `after` = id of the last item from the previous page; the returned
        `next_cursor` is None when there are no more rows."""
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if limit is not None:
            params["limit"] = limit
        raw = self._http.get(routes.feeds.items(feed_id), params=params or None)
        return FeedItemListResponse.model_validate(raw)

    def get_item(self, item_id: str) -> FeedItemWire:
        """GET one feed item by its own (globally unique) ULID, account-scoped."""
        raw = self._http.get(routes.feed_items.detail(item_id))
        return FeedItemWire.model_validate(raw)

    # ── Sources sub-resource ───────────────────────────────────────────

    def list_sources(self, feed_id: str) -> list[SourceWire]:
        raw = self._http.get(routes.feeds.sources(feed_id))
        items = (raw or {}).get("items") or []
        return [SourceWire.model_validate(it) for it in items]

    def get_source(self, source_id: str) -> SourceWire:
        """GET one source by its own (globally unique) ULID; the server resolves
        its feed (sources address by own id, not feed-scoped)."""
        raw = self._http.get(routes.feed_sources.detail(source_id))
        return SourceWire.model_validate(raw)

    def set_sources(
        self,
        feed_id: str,
        sources: list[dict[str, Any]],
        *,
        dry_run: bool = False,
    ) -> SourceSetResult:
        raw = self._http.put(
            routes.feeds.sources(feed_id),
            json_body={"sources": sources, "dry_run": dry_run},
        )
        return SourceSetResult.model_validate(raw)

    def delete_source(self, source_id: str) -> None:
        # By the source's own id; the server resolves its feed (sources address
        # by own id now, not feed-scoped).
        self._http.delete(routes.feed_sources.detail(source_id))
