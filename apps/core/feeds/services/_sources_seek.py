"""`SourceSeekMixin`: the operator-initiated watermark move, mixed into
`SourceService` (which provides `account_id` + the scoping / curated guards).

A seek sets `last_event_at` on every row in scope to one target instant, in
EITHER direction. Forward: the next poll starts at the target (a poll already
in flight may still record that window once; its own advance then loses the
compare-and-set in `advance_watermark`, so the seek survives). Backward: the
next poll re-fetches from the target; items still in the log dedup on external
id, which is why the target may not predate the feed's retention window
(`enforce_seek_floor`). Split out of `sources.py` to keep each module under the
length cap; it is part of the same service, not a second one.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from feeds.configs import CuratedFeedConfig
from feeds.models import Feed, Source
from feeds.policy import PolicyError, SourceWatermarkError, enforce_seek_floor, enforce_source_watermark, seek_floor
from feeds.registry import load_config
from feeds.services.feeds import FeedService
from openmagpie_schema.feed import SourceSeekResult
from openmagpie_schema.run_windows import resolve_window_value


class SourceSeekMixin:
    """Mixed into `SourceService`, which provides `self.account_id` and the
    helpers stubbed under TYPE_CHECKING below (checker-only declarations; at
    runtime the MRO resolves them to the service's real methods)."""

    account_id: str

    if TYPE_CHECKING:

        def _scoped(self, feed: Feed) -> QuerySet[Source]: ...
        def _assert_curated(self, feed: Feed) -> None: ...
        def get_by_id(self, source_id: str, /) -> Source: ...

    @staticmethod
    def _resolve_target(feed: Feed, raw_to: str) -> datetime:
        """Resolve + guard a seek target, on ONE clock read. The raw value goes
        through the shared window resolver with the feed's retention floor as
        the `oldest` referent, so `oldest` is always exactly the deepest allowed
        target. Then the two policy guards: not in the future (a future
        watermark silently disables the source) and not older than that same
        floor (items that far back were pruned and would be re-recorded as new;
        see `enforce_seek_floor`). Resolving and checking against the same `now`
        means an explicit `<retention>d` lands ON the floor and passes, instead of
        microseconds below a floor computed a moment later. `load_config` is what
        the poller's prune reads `retention_days` from, so floor and prune agree
        by construction. Raises `SourceWatermarkError` (HTTP 400 keyed at `to`)
        for a malformed or rejected target, plain `PolicyError` (keyed at
        `detail`) for a feed without retention."""
        # Narrow to the curated config (the poller does the same, polling.py)
        # so `retention_days` is a TYPED read: a renamed field fails ty rather
        # than silently switching the floor off, which a getattr default would.
        # `_assert_curated` already ran, so this is defensive, not reachable.
        config = load_config(feed)
        if not isinstance(config, CuratedFeedConfig):
            raise PolicyError(f"seek needs a feed with a retention window (kind={feed.kind!r})")
        now = timezone.now()
        floor = seek_floor(retention_days=config.retention_days, now=now)
        try:
            to = resolve_window_value(raw_to, now=now, floor=floor)
        except ValueError as exc:
            # A malformed value is about `to` as much as a rejected one is; the
            # subclass keys it there. (Bare ValueError out of a service is
            # reserved for server bugs, which the view must NOT turn into 400s.)
            raise SourceWatermarkError(str(exc)) from None
        to = enforce_source_watermark(to, field="to")
        return enforce_seek_floor(to, retention_days=config.retention_days, now=now)

    @staticmethod
    def _seek_rows(qs: QuerySet[Source], *, to: datetime, dry_run: bool) -> SourceSeekResult:
        """Set `last_event_at = to` on every row of an already account-scoped
        queryset, tallying the move per direction (the operator sees what a seek
        DOES before confirming, not just that it happened).

        Forward set = `lt | isnull` (a NULL watermark bootstraps forward, same
        reading as `advance_watermark`); backward set = `gt`; rows exactly at `to`
        are left alone. The real run issues the two UPDATEs inside one atomic
        block and reports THEIR row counts (authoritative: a concurrent poll's
        `advance_watermark` between a count and the write can't skew the tally,
        the way count-then-update would); `source_count` is the sum of the three
        tallies, never a separate COUNT. Pure column UPDATEs, no JSONB rewrite.
        No `feed_set_lock`: a concurrent `set_sources` only changes WHICH rows
        exist, never racing on the same column. No `poll_lock` either: an
        in-flight poll's advance is a compare-and-set on the mark it read, so it
        matches no row once a seek has moved it (see `advance_watermark`)."""
        forward = qs.filter(Q(last_event_at__lt=to) | Q(last_event_at__isnull=True))
        backward = qs.filter(last_event_at__gt=to)
        at_target = qs.filter(last_event_at=to)
        # All three counts come from inside one atomic block (the real run's two
        # from the UPDATEs themselves) and `source_count` is their SUM, so the
        # invariant `advanced + rewound + unchanged == source_count` holds by
        # construction instead of being clamped into shape after a separate COUNT.
        with transaction.atomic():
            if dry_run:
                advanced, rewound = forward.count(), backward.count()
            else:
                advanced = forward.update(last_event_at=to)
                rewound = backward.update(last_event_at=to)
            # Post-update this is every row (they all equal `to` now), so count
            # the untouched set first on the real run.
            unchanged = at_target.count() if dry_run else at_target.count() - advanced - rewound
        return SourceSeekResult(
            advanced=advanced,
            rewound=rewound,
            unchanged=unchanged,
            source_count=advanced + rewound + unchanged,
            to=to,
            dry_run=dry_run,
        )

    def seek(self, feed: Feed, *, to: str, dry_run: bool = False) -> SourceSeekResult:
        """Move every one of the feed's watermarks to `to` (either direction;
        `_seek_rows` tallies forward vs backward). `to` is the REQUIRED raw target
        string and goes through `_resolve_target`: `oldest` resolves to the feed's retention
        floor; an explicit instant must not be in the future nor older than that
        floor (`SourceWatermarkError`, HTTP 400 keyed at `to`). Never the create
        path's "missing means now" default, so a seek can't silently
        fast-forward a feed. A non-curated feed is a plain `PolicyError` (keyed
        at `detail`). Backs `POST /v1/feeds/<id>/sources/seek`."""
        self._assert_curated(feed)
        target = self._resolve_target(feed, to)
        return self._seek_rows(self._scoped(feed), to=target, dry_run=dry_run)

    def _resolve_owned_source(self, source_id: str, /) -> tuple[Source, Feed]:
        """Resolve a source by its OWN id together with its owning feed, and
        verify that feed is curated. The one seam behind every by-own-id
        mutation (`remove_by_id`, `seek_by_id`). Raises `Source.DoesNotExist` if
        the source is absent / another account's, OR if the feed vanished between
        the two reads (FeedService.delete's atomic cleanup took the source with
        it, so 404 is the truthful answer); `PolicyError` if the feed isn't
        curated. The feed read goes through `FeedService` (the owning service;
        it composes SourceService lazily, so there is no import cycle)."""
        source = self.get_by_id(source_id)
        try:
            feed = FeedService(account_id=self.account_id).get(source.feed_id)
        except Feed.DoesNotExist as exc:
            raise Source.DoesNotExist from exc
        self._assert_curated(feed)
        return source, feed

    def seek_by_id(self, source_id: str, /, *, to: str, dry_run: bool = False) -> SourceSeekResult:
        """`seek` for ONE source by its OWN id (account-scoped). Resolves the
        source + its owning feed via `_resolve_owned_source` (the guard
        `remove_by_id` shares), then moves just that row through the same
        `_resolve_target` as `seek` (`oldest`, future check, retention floor).
        Raises `Source.DoesNotExist` if absent / another account's (HTTP -> 404),
        `SourceWatermarkError` for a rejected target (400 at `to`) and plain
        `PolicyError` for a non-curated feed (400 at `detail`). Backs
        `POST /v1/feed-sources/<id>/seek`."""
        source, feed = self._resolve_owned_source(source_id)
        target = self._resolve_target(feed, to)
        return self._seek_rows(
            self._scoped(feed).filter(id=source.id),
            to=target,
            dry_run=dry_run,
        )
