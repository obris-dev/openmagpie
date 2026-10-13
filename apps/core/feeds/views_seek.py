"""HTTP entry points for the watermark seek (`magpie feed source seek`).

`FeedSourcesSeekView` is `POST /v1/feeds/<id>/sources/seek` (every source on
the feed); `SourceSeekView` is `POST /v1/feed-sources/<id>/seek` (one source by
its own id). Both take `{to, dry_run?}`, resolve `to` on the SERVER clock, and
return the per-direction `SourceSeekResult`. Split from `views.py` (the CRUD
surface) so each module stays one concern, like `watches/views_backfill.py`.
"""

from __future__ import annotations

from collections.abc import Callable

from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from openmagpie_schema.feed import SourceSeekResult

from .api import FeedScopedAPIView, SourceNotFound, SourceSvcMixin
from .models import Source
from .policy import PolicyError, SourceWatermarkError
from .serializers import SourceSeekSerializer


def _seek_or_400(run: Callable[[str], SourceSeekResult], raw_to: str) -> Response:
    """Shared seek dispatch for the two seek views: hand the RAW `to` to the
    service (it resolves the value on its own clock, with the feed's retention
    floor as the `oldest` referent) and map each operator-input failure to the
    key it is about. A malformed or rejected target is `SourceWatermarkError`,
    keyed at `to`; any other `PolicyError` is about the FEED (a kind with no
    sources) and lands at `detail`, the shape the sibling `SourceDetailView.delete`
    uses. A bare `ValueError` out of the service is a server bug (the scope
    guard) and is deliberately NOT caught, so it surfaces as a 500.

    Both views hand `request.data` straight to the serializer: a non-object JSON
    body (a bare array, a string) is a serializer-level 400, so no hand-rolled
    `isinstance` guard is needed, unlike `FeedSourcesView.put` in `views.py`,
    which reads its body by hand."""
    try:
        result = run(raw_to)
    except SourceWatermarkError as exc:
        return Response({"to": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
    except PolicyError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(result.model_dump(mode="json"), status=status.HTTP_200_OK)


class FeedSourcesSeekView(SourceSvcMixin, FeedScopedAPIView):
    """POST /v1/feeds/<id>/sources/seek: move EVERY source watermark on the feed to
    `to` (body `{to: raw window value, dry_run?: bool}`; `to` required), either direction. Returns
    the per-direction tally (`SourceSeekResult`); `dry_run` previews it without
    writing. A verb route (not a PATCH on the collection) because the operation is
    a move with a preview, not a field set."""

    def post(self, request, feed_id: str):
        # A non-object JSON body is a serializer-level 400 ("Expected a
        # dictionary"); see `_seek_or_400`'s note on why there is no guard.
        serializer = SourceSeekSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        return _seek_or_400(
            lambda to: self.source_svc.seek(self.feed, to=to, dry_run=d["dry_run"]),
            d["to"],
        )


class SourceSeekView(SourceSvcMixin, AccountScopedAPIView):
    """POST /v1/feed-sources/<source_id>/seek: move ONE source's watermark by its
    own ULID (the feed is resolved server-side). Same body + result as the
    feed-wide seek, scoped to a single row."""

    def post(self, request, source_id: str):
        # A non-object JSON body is a serializer-level 400 ("Expected a
        # dictionary"); see `_seek_or_400`'s note on why there is no guard.
        serializer = SourceSeekSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        try:
            return _seek_or_400(
                lambda to: self.source_svc.seek_by_id(source_id, to=to, dry_run=d["dry_run"]),
                d["to"],
            )
        except Source.DoesNotExist as exc:
            raise SourceNotFound(source_id) from exc
