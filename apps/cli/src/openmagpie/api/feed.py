"""Feeds API resource client.

Wraps the `/v1/feeds` endpoints. Response
models live ONCE in the shared `openmagpie_schema.feed` package
(populated by the server, imported verbatim here). Only `FeedEnvelope`
(the request envelope the CLI *constructs*) is CLI-owned.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openmagpie_schema.feed import (
    FeedItemListResponse,
    FeedItemWire,
    FeedListResponse,
    FeedMutationResponse,
    FeedView,
    FeedWire,
    SourceInput,
    SourceSetResult,
    SourceWire,
)
from openmagpie_schema.wire import ConfigBlob

from .. import routes
from ..http import MagpieClient

__all__ = [
    "FeedApi",
    "FeedEnvelope",
    "FeedItemListResponse",
    "FeedItemWire",
    "FeedListResponse",
    "FeedMutationResponse",
    "FeedView",
    "FeedWire",
    "SourceSetResult",
    "SourceWire",
]


class FeedEnvelope(BaseModel):
    """The envelope the CLI constructs for a feed write (request side).
    CLI-owned, distinct from the server-emitted models. `data` carries
    the kind-specific config (retention + default_field_map), opaque
    here; the server validates it. `sources` is the optional starter
    source list for curated feeds (server creates Source rows
    atomically with the Feed). Extra keys ignored (so the edit seed's
    read-only fields drop on round-trip)."""

    name: str
    kind: str = "curated"
    poll_interval_seconds: int = 300
    data: ConfigBlob = {}
    sources: list[SourceInput] = []

    model_config = {"extra": "ignore"}


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
