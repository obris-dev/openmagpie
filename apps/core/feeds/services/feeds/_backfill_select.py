"""FeedBackfillSelectMixin: the FeedItem-side source selection backing the
`magpie backfill` flow.

Split out of `FeedItemService` (`_items.py`) so neither file outgrows the 350-line
cap, mirroring the run service's `runs/_backfill_select.py`. The mixin is
account-scoped like its host (it reads `self.account_id`).

Every consumer of a source set gets a DEFINITIVE object, never a raw QuerySet the
caller could re-filter or re-evaluate: a `count_*` (int), a `*_subquery` (a
`Subquery` for `feed_item_id__in=<this>`), or an `iter_*` (a keyset stream). The
private `_*_qs` builders back all three so a count can't drift from its subquery.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import datetime

from django.db.models import QuerySet, Subquery

from feeds.models import FeedItem


class FeedBackfillSelectMixin:
    """FeedItem source selection for the backfill. Mixed into `FeedItemService`,
    which provides `self.account_id`."""

    account_id: str

    def _iter_ids_keyset(self, qs: QuerySet, /, *, page_size: int = 500) -> Iterator[str]:
        """Stream a FeedItem queryset's ids by KEYSET pagination (id > cursor, re-query
        per page). Deliberately NOT `.iterator()`: a server-side cursor is disabled
        under a transaction-pooling pgbouncer (then `.iterator()` materializes the whole
        set) and can dangle across a consumer's interleaved writes. Keyset holds nothing
        server-side and streams under any config, the pattern the trigger uses. O(page)
        memory. Backs the `iter_*` streamers; ids are ULIDs, so id ordering is stable."""
        cursor = ""
        while True:
            page = list(qs.filter(id__gt=cursor).order_by("id").values_list("id", flat=True)[:page_size])
            if not page:
                return
            yield from page
            cursor = page[-1]

    def _existing_feed_item_ids_qs(self, candidate_ids, /) -> QuerySet:
        """Private FeedItem queryset for the public consumers below: this account's
        items whose id intersects `candidate_ids` (itself a `Subquery`/iterable), the
        backfill's PREDECESSOR source intersected with existence (a pruned id is simply
        absent). Kept private so callers get a definitive count / `Subquery` / iterator,
        never a malleable QuerySet to re-filter or re-evaluate."""
        return FeedItem.objects.filter(account_id=self.account_id, id__in=candidate_ids)

    def count_existing_feed_item_ids(self, candidate_ids, /) -> int:
        """How many of `candidate_ids` still exist (the backfill's `present` size)."""
        return self._existing_feed_item_ids_qs(candidate_ids).count()

    def existing_feed_item_ids_subquery(self, candidate_ids, /) -> Subquery:
        """The surviving ids as a `Subquery` for `feed_item_id__in=<this>` (delete +
        count, server-side, nothing materialized in Python). A `Subquery` expression,
        NOT a QuerySet, so the caller can only use it as an `__in` operand."""
        return Subquery(self._existing_feed_item_ids_qs(candidate_ids).values("id"))

    def iter_existing_feed_item_ids(self, candidate_ids, /) -> Iterator[str]:
        """Stream the surviving ids (the backfill enqueue), keyset-paginated."""
        return self._iter_ids_keyset(self._existing_feed_item_ids_qs(candidate_ids))

    def _feed_items_in_occurred_window_qs(
        self, *, feed_ids: Iterable[str], since: datetime | None, until: datetime | None
    ) -> QuerySet:
        """Private FeedItem queryset for the public consumers below: this account's
        items under `feed_ids` whose source time falls in `[since, until)`, the
        CHAIN-HEAD backfill source (what the trigger would have fed a rank-0 action,
        feed-scoped to the watch's feeds; all trivially exist). Kept private so callers
        get a definitive count / `Subquery` / iterator. >=1 bound required; NULL
        `occurred_at` -> excluded."""
        if since is None and until is None:
            raise ValueError("feed_items_in_occurred_window needs at least one of since / until")
        qs = FeedItem.objects.filter(account_id=self.account_id, feed_id__in=list(feed_ids))
        if since is not None:
            qs = qs.filter(occurred_at__gte=since)
        if until is not None:
            qs = qs.filter(occurred_at__lt=until)
        return qs

    def count_feed_items_in_occurred_window(
        self, *, feed_ids: Iterable[str], since: datetime | None, until: datetime | None
    ) -> int:
        """How many of the watch's feed items fall in the window (matched == present
        for a chain-head source; all trivially exist)."""
        return self._feed_items_in_occurred_window_qs(feed_ids=feed_ids, since=since, until=until).count()

    def feed_items_in_occurred_window_subquery(
        self, *, feed_ids: Iterable[str], since: datetime | None, until: datetime | None
    ) -> Subquery:
        """The window's ids as a `Subquery` for `feed_item_id__in=<this>` (see
        `existing_feed_item_ids_subquery` for the Subquery-not-QuerySet rationale)."""
        return Subquery(
            self._feed_items_in_occurred_window_qs(feed_ids=feed_ids, since=since, until=until).values("id")
        )

    def iter_feed_items_in_occurred_window(
        self, *, feed_ids: Iterable[str], since: datetime | None, until: datetime | None
    ) -> Iterator[str]:
        """Stream the window's ids (the chain-head backfill enqueue), keyset-paginated."""
        return self._iter_ids_keyset(
            self._feed_items_in_occurred_window_qs(feed_ids=feed_ids, since=since, until=until)
        )
