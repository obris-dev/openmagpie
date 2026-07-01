"""Watch trigger pass: enqueue first-action runs for new feed items.

Mirrors `FeedPollOperation`, a one-shot operation built from one Watch,
`.run()` once. The `process_due_watches` cron iterates active watches and
runs one of these each.

Pull model (not push): the per-(watch, feed) watermark on `WatchFeed` is
the cursor. For each subscribed feed the operation scans FeedItems past
that watermark, enqueues a PENDING `WatchActionRun` for the chain's FIRST
action (rank 0), then advances the watermark to the scan's upper bound.
Because the cursor lives on the watch side, a watch created after items
already exist still picks them up (a blank watermark backfills the whole
retained window), and `feeds` never needs to know about its watchers.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property

from django.db import transaction
from django.utils import timezone

from feeds.models import Feed
from feeds.services import FeedItemService, FeedService
from openmagpie_schema.watch_actions import DeliveryConfigBase
from watches.models import Watch
from watches.registry import load_config
from watches.services import WatchActionRunService, WatchDigestWindowService, WatchService

logger = logging.getLogger("watches")


@dataclass(frozen=True)
class FeedTriggered:
    """Per-feed progress, emitted once after each subscribed feed is scanned."""

    watch: Watch
    feed_id: str
    enqueued: int


WatchTriggerProgressCallback = Callable[[FeedTriggered], None]


@dataclass(frozen=True)
class WatchTriggerResult:
    feeds_scanned: int
    runs_enqueued: int


class WatchTriggerOperation:
    """One-shot: scan a Watch's subscribed feeds, enqueue first-action runs."""

    def __init__(self, watch: Watch, *, on_progress: WatchTriggerProgressCallback | None = None) -> None:
        self.watch = watch
        self.account_id = str(watch.account_id)
        self.on_progress: WatchTriggerProgressCallback = on_progress or (lambda _: None)

    @cached_property
    def watch_svc(self) -> WatchService:
        return WatchService(account_id=self.account_id)

    @cached_property
    def run_svc(self) -> WatchActionRunService:
        return WatchActionRunService(account_id=self.account_id)

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.account_id)

    @cached_property
    def digest_svc(self) -> WatchDigestWindowService:
        return WatchDigestWindowService(account_id=self.account_id)

    def run(self) -> WatchTriggerResult:
        now = timezone.now()

        # The chain's first action is the only enqueue target ; the drain
        # advances to the next-greater rank on success. `head` is the lowest
        # rank by ORDER (initial_actions is rank-ordered, head = [0]), NOT a
        # `rank == 0` lookup, so this stays correct if ranks ever go sparse.
        # An actionless watch has nothing to trigger (a legit empty chain,
        # not corruption).
        actions = self.watch_svc.initial_actions(self.watch)
        if not actions:
            return WatchTriggerResult(feeds_scanned=0, runs_enqueued=0)
        head = actions[0]
        first_action_id = str(head.id)
        # A digest head has no preceding action to open its window on advance,
        # so the trigger opens it here — else claim_due (which excludes only
        # windowed actions) would deliver these rank-0 runs instant instead of
        # batching them. Opened lazily on the first feed with new items
        # (`_digest_scheduled_at`), so a no-op cycle doesn't churn a window.
        head_cfg = load_config(head)
        if isinstance(head_cfg, DeliveryConfigBase) and head_cfg.is_digest():
            head_is_digest = True
            digest_interval = head_cfg.digest_interval_seconds
        else:
            head_is_digest = False
            digest_interval = 0
        digest_scheduled_at: datetime | None = None

        feeds_scanned = 0
        runs_enqueued = 0
        for watch_feed in self.watch_svc.watch_feeds(self.watch):
            try:
                feed = FeedService.Global.get(watch_feed.feed_id)
            except Feed.DoesNotExist:
                # Subscription to a deleted feed: skip, leave the row
                # (cleanup is a watch-edit concern). One stale feed must
                # not abort the watch's others.
                logger.warning(
                    "watch=%s subscribed to missing feed=%s; skipping",
                    self.watch.id,
                    watch_feed.feed_id,
                )
                continue

            feeds_scanned += 1
            through_id = self.feed_item_svc.newest_item_id(feed)
            if through_id is None:
                continue  # empty feed, nothing to scan

            # Blank watermark backfills the retained window (after_id="" <
            # every ULID). Already at head -> no new items this cycle.
            after_id = watch_feed.last_item_id or ""
            if after_id == through_id:
                continue

            # A digest head: ensure its window is open BEFORE the runs land
            # (so claim_due excludes them), in its own short txn — opened once
            # per cycle and shared by every feed. scheduled_at is the window
            # close, mirroring the advance path. Instant head: scheduled now.
            if head_is_digest:
                if digest_scheduled_at is None:
                    with transaction.atomic():
                        digest_scheduled_at = self.digest_svc.open_window(
                            first_action_id, interval_seconds=digest_interval, now=now
                        )
                scheduled_at = digest_scheduled_at
            else:
                scheduled_at = now

            # The enqueue itself is NOT transactional, deliberately:
            # enqueue-before-advance keeps the watermark from moving past
            # un-enqueued items, and the only crash gap (enqueued, not
            # advanced) self-heals next cycle since enqueue_many is idempotent.
            # A transaction would hold one write lock across the whole chunked
            # backfill.
            enqueued = self.run_svc.enqueue_many(
                watch_id=str(self.watch.id),
                action_id=first_action_id,
                kind=str(head.kind),
                feed_item_ids=self.feed_item_svc.iter_item_ids_in_window(
                    feed, after_id=after_id, through_id=through_id
                ),
                scheduled_at=scheduled_at,
            )
            self.watch_svc.advance_watermark(watch_feed, last_item_id=through_id)
            runs_enqueued += enqueued
            self.on_progress(FeedTriggered(self.watch, str(feed.id), enqueued))

        return WatchTriggerResult(feeds_scanned=feeds_scanned, runs_enqueued=runs_enqueued)
