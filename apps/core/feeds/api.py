"""Feed-scope DRF mixin.

For endpoints under
`/v1/feeds/<feed_id>/...` ids land on the request
(`request.account_id` from the parent mixin, `request.feed_id` from the
URL pattern); the resolved services and row are cached_properties on
the view (`self.feed_svc`, `self.source_svc`, `self.feed`) so they're
loaded once per request, lazily, and not muddled into the request
namespace.

Handlers read `self.feed` directly. If the row is absent the
cached_property raises `FeedNotFound`, which DRF's exception handler
turns into a 404 response automatically ; no manual `if isinstance(x,
Response): return x` dance at the top of every method.
"""

from __future__ import annotations

from functools import cached_property

from rest_framework import status
from rest_framework.exceptions import APIException

from accounts.api import AccountScopedAPIView, AccountScopedRequest

from .models import Feed, Source
from .services import FeedItemService, FeedService
from .services.sources import SourceService


class FeedScopedRequest(AccountScopedRequest):
    """Typing view of the request once `feed_id` is stashed by
    `FeedScopedAPIView.initial()`."""

    feed_id: str


class SourceScopedRequest(FeedScopedRequest):
    """Typing view of the request once `source_id` is stashed by
    `SourceScopedAPIView.initial()`."""

    source_id: str


class FeedNotFound(APIException):
    """404 for a feed absent from the caller's account. Same body
    whether it never existed or belongs to another account ; not
    distinguishing IS the account-scoping guarantee."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, feed_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no feed {feed_id}"},
            code=self.default_code,
        )


class SourceNotFound(APIException):
    """404 for a Source row absent from the (account, feed) it's
    scoped to. Account scoping is again opaque ; the body looks the
    same whether the id never existed or sits on another feed."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, source_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no source {source_id}"},
            code=self.default_code,
        )


class FeedItemNotFound(APIException):
    """404 for a FeedItem absent from the caller's account (never existed,
    pruned, or another account's). Opaque, like the others."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, item_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no item {item_id}"},
            code=self.default_code,
        )


class FeedSvcMixin:
    """Per-request `feed_svc` cached_property ; usable on any view that
    knows the account but doesn't have a feed-id in its URL."""

    request: AccountScopedRequest

    @cached_property
    def feed_svc(self) -> FeedService:
        return FeedService(account_id=self.request.account_id)


class SourceSvcMixin:
    """Per-request `source_svc` cached_property for the sources views."""

    request: AccountScopedRequest

    @cached_property
    def source_svc(self) -> SourceService:
        return SourceService(account_id=self.request.account_id)


class FeedItemSvcMixin:
    """Per-request `feed_item_svc` cached_property ; for views that
    need to read or write the feed's item log (GET-detail recent
    items, the prune path, ...)."""

    request: AccountScopedRequest

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.request.account_id)


class FeedScopedAPIView(FeedSvcMixin, AccountScopedAPIView):
    """APIView for endpoints scoped to one feed within the authenticated
    user's account.

    After `initial()` runs:
      - `request.account_id: str` ; from AccountScopedAPIView
      - `request.feed_id: str` ; from the URL `<str:feed_id>` capture

    Verb methods access the resolved objects via:
      - `self.feed_svc: FeedService` ; account-scoped
      - `self.feed: Feed` ; the resolved row, raises FeedNotFound on
        first access if the row is absent

    Both are `cached_property`, so a verb method that touches
    `self.feed` twice incurs one DB hit, and a verb method that never
    touches it skips the lookup entirely (e.g. on `magpie feed delete`
    we still need it; on a future no-lookup endpoint we wouldn't)."""

    # `initial()` stashes `feed_id` on the live request; narrow the
    # annotation so handlers read `self.request.feed_id` as `str`.
    request: FeedScopedRequest

    def initial(self, request, *args, **kwargs):
        # super() runs auth + account scope; `request.account_id` is
        # populated by the time control returns.
        super().initial(request, *args, **kwargs)
        # The `<str:feed_id>` URL capture always provides this; index
        # (not .get) so the type is `str`, not `str | None`.
        request.feed_id = kwargs["feed_id"]

    @cached_property
    def feed(self) -> Feed:
        try:
            return self.feed_svc.get(self.request.feed_id)
        except Feed.DoesNotExist as exc:
            raise FeedNotFound(self.request.feed_id) from exc


class SourceScopedAPIView(SourceSvcMixin, FeedScopedAPIView):
    """APIView for endpoints scoped to one Source row inside a feed.

    Extends FeedScopedAPIView with a stashed `source_id` and a
    `self.source` cached_property that raises `SourceNotFound` on
    miss (DRF -> 404). Touch `self.feed` (auto-404 on a missing
    feed) before mutating to keep the feed-scoping guarantee
    explicit; the source lookup is account + feed bounded inside
    `SourceService.get`."""

    request: SourceScopedRequest

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # `<str:source_id>` URL capture is always present; index for `str`.
        request.source_id = kwargs["source_id"]

    @cached_property
    def source(self) -> Source:
        try:
            return self.source_svc.get(self.feed, source_id=self.request.source_id)
        except Source.DoesNotExist as exc:
            raise SourceNotFound(self.request.source_id) from exc
