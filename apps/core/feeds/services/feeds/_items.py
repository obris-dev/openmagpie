"""FeedItemService: account-scoped reads + writes on the items log.

The Feed row itself is owned by FeedService (CRUD); this service owns
everything about the items that accumulate under a Feed: recording new
items on poll, pruning to the retention window, and the read queries
the judge cycle and detail views need.
"""

import builtins
import itertools
import logging
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta

from django.db.models import Subquery
from django.utils import timezone

from common.db import ID_IN_CHUNK
from common.fields import min_ulid_at
from feeds.models import Feed, FeedItem
from sources.payloads import SourcePayload

from ._backfill_select import FeedBackfillSelectMixin

logger = logging.getLogger("feeds")


class FeedItemService(FeedBackfillSelectMixin):
    """Account-scoped service for FeedItem reads + writes against a Feed."""

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("FeedItemService requires account_id")
        self.account_id = account_id

    def _assert_scope(self, account_id: str, what: str) -> None:
        if account_id != self.account_id:
            raise ValueError(f"{what} account_id mismatch: {account_id!r} not in scope {self.account_id!r}")

    def record_items(
        self,
        feed: Feed,
        /,
        *,
        source_label: str,
        payloads: Iterable[SourcePayload],
        source_meta: dict[str, str] | None = None,
        chunk_size: int = 200,
    ) -> int:
        """Persist a source's polled items as FeedItems. Idempotent on
        (feed_id, source_kind, external_id); returns the count of NEW rows.

        `source_meta` is copied onto each persisted FeedItem; defaults
        to empty when the polling path hasn't matched a Source row yet.

        Streams the iterable in fixed-size chunks so memory stays
        O(chunk_size). Per chunk: one SELECT to find existing keys, one
        bulk_create for the new rows; O(N / chunk_size) round trips
        instead of O(N). The feed's poll_lock is held during the whole
        cycle, so the SELECT->INSERT window has no race risk.
        """
        self._assert_scope(str(feed.account_id), "feed")
        meta = source_meta or {}
        created = 0
        chunk: list[SourcePayload] = []
        for payload in payloads:
            chunk.append(payload)
            if len(chunk) >= chunk_size:
                created += self._record_chunk(feed, source_label=source_label, source_meta=meta, chunk=chunk)
                chunk = []
        if chunk:
            created += self._record_chunk(feed, source_label=source_label, source_meta=meta, chunk=chunk)
        return created

    def _record_chunk(
        self,
        feed: Feed,
        /,
        *,
        source_label: str,
        source_meta: dict[str, str],
        chunk: list[SourcePayload],
    ) -> int:
        """Persist one chunk: SELECT existing keys, bulk_create the rest.
        Returns the count of new rows in this chunk."""
        external_ids = [payload.external_id for payload in chunk]
        existing = set(
            FeedItem.objects.filter(
                account_id=self.account_id, feed_id=feed.id, external_id__in=external_ids
            ).values_list("source_kind", "external_id")
        )
        rows = [
            FeedItem(
                account_id=self.account_id,
                feed_id=feed.id,
                source_kind=payload.source,
                external_id=payload.external_id,
                source_label=source_label,
                source_meta=source_meta,
                occurred_at=payload.occurred_at,
                data=payload.model_dump(mode="json"),
            )
            for payload in chunk
            if (payload.source, payload.external_id) not in existing
        ]
        if not rows:
            return 0
        # ignore_conflicts as a safety belt: SELECT-then-INSERT is race-free
        # under the feed's poll_lock, but ignore_conflicts means a future
        # bug or lock regression silently dedups instead of aborting the
        # whole chunk. Accurate "new rows" count comes from `rows` filtering.
        FeedItem.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def prune_items(
        self,
        feed: Feed,
        /,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> int:
        """Delete FeedItems older than retention (by ULID-ms cutoff).

        Uses `id < min_ulid_at(cutoff)` instead of `created_at < cutoff` so
        the delete plan hits the `(account_id, feed_id, id)` index as a
        tight range scan. Returns the count deleted.

        NOTE: a subscriber-cursor floor (so items a lagging Watch hasn't
        processed yet aren't silently cut) returns with WatchCursor ; until
        then prune is a plain retention cutoff.
        """
        self._assert_scope(str(feed.account_id), "feed")
        cutoff = (now or timezone.now()) - timedelta(days=retention_days)
        cutoff_ulid = min_ulid_at(cutoff)
        deleted, _ = FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id, id__lt=cutoff_ulid).delete()
        return deleted

    def get(self, item_id: str, /) -> FeedItem:
        """One FeedItem by id, account-scoped. Raises FeedItem.DoesNotExist
        if missing / other-account. The drain loads `item.data` (the stored
        SourcePayload dump) here to hand to an action."""
        return FeedItem.objects.get(id=item_id, account_id=self.account_id)

    def get_many(self, item_ids: Iterable[str], /) -> builtins.dict[str, FeedItem]:
        """The requested FeedItems by id (account-scoped), as {id: item}.
        Missing / other-account ids are simply absent from the map (the caller
        decides what a gone item means). The digest flush uses this to fetch a
        whole batch's items without an N+1.

        Memory is O(number of ids): the result dict holds every found item, so
        the CALLER bounds how many ids it passes (the digest flush caps its
        batch at DIGEST_MAX_BATCH_ITEMS). The `id__in` is chunked only to stay
        under the DB's per-statement parameter ceiling (common.db.ID_IN_CHUNK)
        — that bounds the per-statement width, NOT peak memory."""
        result: builtins.dict[str, FeedItem] = {}
        for chunk in itertools.batched(item_ids, ID_IN_CHUNK, strict=False):
            rows = FeedItem.objects.filter(account_id=self.account_id, id__in=chunk)
            result.update((str(item.id), item) for item in rows)
        return result

    def newest_item_id(self, feed: Feed, /) -> str | None:
        """ULID pk of the newest FeedItem in this feed, or None if empty.
        Used as the snapshot upper bound for a judge cycle."""
        self._assert_scope(str(feed.account_id), "feed")
        return (
            FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id)
            .order_by("-id")
            .values_list("id", flat=True)
            .first()
        )

    def count_items_in_window(
        self,
        feed: Feed,
        /,
        *,
        after_id: str,
        through_id: str,
    ) -> int:
        """How many FeedItems sit in `(after_id, through_id]` for this feed.

        Cheap COUNT query the judge cycle uses to size up work before the
        slow leg (one LLM call per item). Lets the management command
        print a "judging N items (~Ns)" heads-up so the operator knows
        what they're in for. Same window shape as `iter_items_in_window`."""
        self._assert_scope(str(feed.account_id), "feed")
        return FeedItem.objects.filter(
            account_id=self.account_id,
            feed_id=feed.id,
            id__gt=after_id,
            id__lte=through_id,
        ).count()

    def occurred_window_id_subquery(self, *, since: datetime | None, until: datetime | None) -> Subquery:
        """A `Subquery` of the account's FeedItem ids whose SOURCE time
        (`occurred_at`) falls in `[since, until)` -- the run report's occurred-window
        filter (`feed_item_id__in=<this>`; the run table has no FK to FeedItem, so
        the join is by id). Returns a `Subquery`, NOT a QuerySet, so the ids stay
        server-side (one SQL statement, nothing materialized in Python) and the
        caller can't iterate or re-evaluate it. Account-scoped, NOT feed-scoped (the
        report spans the action's items across feeds).

        NULL `occurred_at` rows are excluded by the bound comparisons. `occurred_at`
        is unindexed (see feed_item.py), so a wide window is a residual scan over
        the account's items - retention-bounded, acceptable for the export."""
        # At least one bound is required: both-None would match every item the
        # account has (an unbounded subquery), which no caller wants -- harden the
        # seam rather than trust each future caller to pre-check.
        if since is None and until is None:
            raise ValueError("occurred_window_id_subquery needs at least one of since / until")
        qs = FeedItem.objects.filter(account_id=self.account_id)
        if since is not None:
            qs = qs.filter(occurred_at__gte=since)
        if until is not None:
            qs = qs.filter(occurred_at__lt=until)
        return Subquery(qs.values("id"))

    def iter_items_in_window(
        self,
        feed: Feed,
        /,
        *,
        after_id: str,
        through_id: str,
        chunk_size: int = 200,
    ) -> Iterator[FeedItem]:
        """Yield FeedItems in `(after_id, through_id]` for this feed,
        chronological by ULID pk.

        Slice-based pagination (each chunk a fresh LIMIT query) instead of
        `.iterator()`, so early `break` is safe; no server-side cursor
        to dangle. Memory stays O(chunk_size) regardless of window size;
        the underlying set is also feed-retention-bounded.
        """
        self._assert_scope(str(feed.account_id), "feed")
        last_seen = after_id
        while True:
            chunk = builtins.list(
                FeedItem.objects.filter(
                    account_id=self.account_id,
                    feed_id=feed.id,
                    id__gt=last_seen,
                    id__lte=through_id,
                ).order_by("id")[:chunk_size]
            )
            if not chunk:
                return
            for item in chunk:
                yield item
                last_seen = str(item.id)
            if len(chunk) < chunk_size:
                return

    def iter_item_ids_in_window(
        self,
        feed: Feed,
        /,
        *,
        after_id: str,
        through_id: str,
        chunk_size: int = 500,
    ) -> Iterator[str]:
        """Yield just the ULID pks in `(after_id, through_id]`, ascending.

        The id-only twin of `iter_items_in_window` for callers that need
        only the keys, not the rows ; the watch trigger enqueues runs by
        `feed_item_id` and never reads the payload, so projecting to `id`
        avoids dragging every item's `data` JSON out of the DB. Same
        keyset pagination (O(chunk_size) memory, early-break safe)."""
        self._assert_scope(str(feed.account_id), "feed")
        last_seen = after_id
        while True:
            chunk = builtins.list(
                FeedItem.objects.filter(
                    account_id=self.account_id,
                    feed_id=feed.id,
                    id__gt=last_seen,
                    id__lte=through_id,
                )
                .order_by("id")
                .values_list("id", flat=True)[:chunk_size]
            )
            if not chunk:
                return
            for item_id in chunk:
                yield str(item_id)
                last_seen = str(item_id)
            if len(chunk) < chunk_size:
                return

    def list_for_feed(self, feed: Feed, /, *, after: str | None = None, limit: int = 50) -> builtins.list[FeedItem]:
        """This account's items for one feed, newest-first (ULID pk),
        cursor-paginated for the audit CLI (`feed item list --feed`): pass
        `after=<id>` to fetch rows whose id is strictly less (older), omit for the
        newest page. Scoped by (account, feed)."""
        self._assert_scope(str(feed.account_id), "feed")
        qs = FeedItem.objects.filter(account_id=self.account_id, feed_id=feed.id)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])
