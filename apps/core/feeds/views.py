"""HTTP entry points for /v1/feeds.

`FeedListCreateView` handles POST (create) + GET (list). `FeedDetailView`
handles GET / PUT / DELETE on `/v1/feeds/<id>`; its GET is the "sort
by new and go" reader (returns the feed + its recent items, with
optional ?limit). `FeedSourcesView` + `FeedSourceDetailView` cover the
`/sources` sub-router.

The `/v1/feeds/<id>/...` views inherit `FeedScopedAPIView` and read
`self.feed` directly ; a missing feed raises `FeedNotFound`, DRF
converts to 404, no manual response juggling inside handlers.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.api_params import is_truthy, parse_limit
from common.pydantic_errors import pydantic_errors_to_drf
from openmagpie_schema.feed import FeedItemListResponse, FeedListResponse
from telemetry import events as telemetry_events
from telemetry.constants import Surface

from .api import (
    FeedItemNotFound,
    FeedItemSvcMixin,
    FeedScopedAPIView,
    FeedSvcMixin,
    SourceNotFound,
    SourceSvcMixin,
)
from .models import FeedItem, Source
from .policy import PolicyError
from .serializers import (
    SOURCE_INPUT_LIST_ADAPTER,
    FeedCreateSerializer,
    feed_item_wire,
    feed_mutation,
    feed_view,
    feed_wire,
    source_wire,
)
from .services.sources import ConcurrentSetSourcesError

logger = logging.getLogger("feeds")


class FeedListCreateView(FeedSvcMixin, AccountScopedAPIView):
    """POST /v1/feeds (create), GET /v1/feeds (list)."""

    def post(self, request):
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if is_truthy(request.query_params.get("dry_run")):
            preview = self.feed_svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                kind=d["kind"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            preview_data = feed_mutation(preview, dry_run=True).model_dump(mode="json")
            preview_data.pop("id", None)  # empty placeholder pre-save
            # The preview feed is built WITHOUT persisting Source rows, so it
            # reports zero sources. Surface the count that WOULD be created
            # from the (already-validated) request so the preview isn't a
            # misleading "(0)".
            preview_data["source_count"] = len(d.get("sources") or [])
            return Response(preview_data, status=status.HTTP_200_OK)

        feed = self.feed_svc.create(
            user_id=str(request.user.id),
            name=d["name"],
            kind=d["kind"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
            sources=d.get("sources") or None,
        )
        # Anonymous telemetry (no-op unless opted in). Emitted from this API seam,
        # not the service layer, so the canned quickstart seed (which creates feeds
        # via the service) isn't counted as a user-created feed -- quickstart_completed
        # covers the install. Guarded so a telemetry hiccup never fails the create.
        with telemetry_events.guard():
            if telemetry_events.enabled():  # gather only when opted in (skips the Source query when off)
                kinds = [s.kind for s in self.feed_svc.source_svc.list(feed)]
                telemetry_events.feed_created(
                    source_count=len(kinds),
                    connector_kinds=kinds,
                    surface=getattr(request, "surface", Surface.API.value),
                )
        return Response(
            feed_mutation(feed, dry_run=False).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        limit = parse_limit(request)
        after = request.query_params.get("after") or None
        feeds = self.feed_svc.list(after=after, limit=limit)
        # Page is "full" iff we got `limit` rows; if so, more pages may exist
        # and the last row's id is the cursor for the next page.
        next_cursor = str(feeds[-1].id) if len(feeds) == limit else None
        return Response(
            FeedListResponse(items=[feed_wire(o) for o in feeds], next_cursor=next_cursor).model_dump(mode="json")
        )


class FeedDetailView(FeedScopedAPIView):
    """GET / PUT / DELETE /v1/feeds/<id>, all account-scoped. GET is the feed's
    CONFIG detail (envelope + summary + sources); the item log is a separate
    paginated route (`GET /v1/feeds/<id>/items`)."""

    def get(self, request, feed_id: str):
        return Response(
            feed_view(self.feed).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def put(self, request, feed_id: str):
        serializer = FeedCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if d["kind"] != self.feed.kind:
            return Response(
                {
                    "error": "kind_immutable",
                    "detail": f"feed kind is {self.feed.kind!r} and cannot be changed (requested {d['kind']!r})",
                },
                status=status.HTTP_409_CONFLICT,
            )

        edit_kwargs = {
            "name": d["name"],
            "poll_interval_seconds": d["poll_interval_seconds"],
            "data": d["data"],
        }
        # Policy runs on the merged config inside build_update/update;
        # map PolicyError -> 400 (same shape create uses).
        try:
            if is_truthy(request.query_params.get("dry_run")):
                preview = self.feed_svc.build_update(self.feed, **edit_kwargs)
                return Response(
                    feed_mutation(preview, dry_run=True).model_dump(mode="json"),
                    status=status.HTTP_200_OK,
                )
            updated = self.feed_svc.update(self.feed, **edit_kwargs)
            return Response(
                feed_mutation(updated, dry_run=False).model_dump(mode="json"),
                status=status.HTTP_200_OK,
            )
        except PolicyError as exc:
            return Response({"data": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, feed_id: str):
        self.feed_svc.delete(self.feed)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FeedSourcesView(SourceSvcMixin, FeedScopedAPIView):
    """Sources sub-router on `/v1/feeds/<id>/sources`.

      GET  list the feed's sources
      PUT  set/replace the whole list (body = {sources: [SourceInput], dry_run?})

    Per-row DELETE lives on `FeedSourceDetailView` so the URL keys the
    target row directly. Single-row add is intentionally absent ; the
    create-time path is the inline `sources:` block on `feed create`,
    and ongoing mutation is `feed source export -> edit -> feed source set`."""

    def get(self, request, feed_id: str):
        return Response(
            {"items": [source_wire(s).model_dump(mode="json") for s in self.source_svc.list(self.feed)]},
            status=status.HTTP_200_OK,
        )

    def put(self, request, feed_id: str):
        # DRF happily parses a bare top-level JSON array (CLI's own
        # `_parse_set_payload` accepts that shape, so a hand-rolled
        # client easily sends it). `.get(...)` on a list raises
        # AttributeError -> 500. Reject up front with a 400 naming the
        # required shape.
        body = request.data
        if not isinstance(body, dict):
            return Response(
                {"detail": "request body must be a JSON object with a `sources` array"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            items = SOURCE_INPUT_LIST_ADAPTER.validate_python(body.get("sources") or [])
        except PydanticValidationError as exc:
            return Response({"sources": pydantic_errors_to_drf(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # `dry_run` is a JSON bool. Reject anything else: `bool("false")`
        # is True (non-empty string), so a hand-rolled client sending
        # the string `"false"` would silently flip to dry-run and the
        # operator would see "would: ..." instead of the real apply.
        # Accept only a real bool (or absent, defaults to False).
        raw_dry_run = body.get("dry_run", False)
        if not isinstance(raw_dry_run, bool):
            return Response(
                {"detail": "`dry_run` must be a JSON boolean (true or false)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dry_run = raw_dry_run
        try:
            result = self.source_svc.set_sources(self.feed, items, dry_run=dry_run)
        except PolicyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except ConcurrentSetSourcesError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(result.model_dump(mode="json"), status=status.HTTP_200_OK)


class FeedItemsView(FeedItemSvcMixin, FeedScopedAPIView):
    """GET /v1/feeds/<id>/items: the feed's item log, newest-first, cursor-
    paginated (`?after=`, `?limit=`). The feed-scoped list complement to the
    by-own-id detail at /v1/feed-items/<id> ; items are read-only (no write
    verbs), so this is the only mutation-free sub-router on a feed."""

    def get(self, request, feed_id: str):
        limit = parse_limit(request)
        after = request.query_params.get("after") or None
        items = self.feed_item_svc.list_for_feed(self.feed, after=after, limit=limit)
        next_cursor = str(items[-1].id) if len(items) == limit else None
        return Response(
            FeedItemListResponse(items=[feed_item_wire(i) for i in items], next_cursor=next_cursor).model_dump(
                mode="json"
            )
        )


class FeedItemDetailView(FeedItemSvcMixin, AccountScopedAPIView):
    """GET /v1/feed-items/<item_id>: one feed item by its own (globally unique)
    ULID, account-scoped. A feed item is a dependent record of its feed, so it is
    parent-qualified ; the list (lean rows) lives nested at /v1/feeds/<id>/items.
    Read-only."""

    def get(self, request, item_id: str):
        try:
            item = self.feed_item_svc.get(item_id)
        except FeedItem.DoesNotExist as exc:
            raise FeedItemNotFound(item_id) from exc
        return Response(feed_item_wire(item).model_dump(mode="json"))


class SourceDetailView(SourceSvcMixin, AccountScopedAPIView):
    """GET / DELETE /v1/feed-sources/<source_id>: one source by its own (globally
    unique) ULID, account-scoped ; the feed it belongs to is resolved server-side,
    not supplied by the caller. The feed-scoped set lives at /v1/feeds/<id>/sources."""

    def get(self, request, source_id: str):
        try:
            source = self.source_svc.get_by_id(source_id)
        except Source.DoesNotExist as exc:
            raise SourceNotFound(source_id) from exc
        return Response(source_wire(source).model_dump(mode="json"))

    def delete(self, request, source_id: str):
        try:
            self.source_svc.remove_by_id(source_id)
        except Source.DoesNotExist as exc:
            raise SourceNotFound(source_id) from exc
        except PolicyError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)
