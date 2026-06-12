"""Feed poll orchestrator: fetch each source, persist items, prune.

The Feed owns polling (Watches judge the resulting items). For each
Source row on the feed this fetches via the connector keyed by
`spec.kind`, persists new items as FeedItems (idempotent), advances
the row's `last_event_at` watermark, and prunes the item log to the
retention window.

Per-source `last_event_at` is non-None by invariant: feed-config
policy fills it with wall-clock now at save time so the first poll
fetches real items (no cold-start "set watermark, fetch nothing"
trip). An operator who wants historical context passes an explicit
past datetime on the SourceInput at create.

`FeedPollOperation` is a one-shot operation; build with a Feed and call
`.run()` once.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from typing import TYPE_CHECKING

import httpx
from django.utils import timezone
from pydantic import TypeAdapter, ValidationError

from common.locks import poll_lock
from feeds.configs import CuratedFeedConfig
from feeds.models import Feed, Source
from feeds.registry import load_config
from openmagpie_schema.configs import SourceSpec
from sources import registry as source_registry
from sources.connectors.base import ConnectorParseError
from sources.payloads import SourcePayload

from .feeds import FeedItemService, FeedService

_SPEC_ADAPTER = TypeAdapter(SourceSpec)

if TYPE_CHECKING:
    from .sources import SourceService

logger = logging.getLogger("feeds")

# Per-source failures we expect and recover from (one bad source must not
# abort the whole feed cycle). Anything else is a bug and should propagate.
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
    ConnectorParseError,
    # `source_registry.get(spec.kind)` raises bare KeyError when the
    # connector for that kind isn't loaded in this deployment (e.g. a
    # config-only spec like `rss` whose connector ships later). Without
    # this, the whole cycle aborts on the first unregistered kind ;
    # last_polled_at never advances and the feed stays perpetually due.
    KeyError,
)


@dataclass(frozen=True)
class SourcePolled:
    """Per-source progress, emitted once after each source is polled."""

    feed: Feed
    source_display: str
    observed: int
    recorded: int


FeedPollProgressCallback = Callable[[SourcePolled], None]


@dataclass(frozen=True)
class FeedPollResult:
    observed: int
    recorded: int
    pruned: int


def _ensure_aware(value: datetime) -> datetime:
    """Tag a naive datetime as UTC; pass through aware datetimes
    untouched. Defensive belt for the `_PolledSource.newest`
    comparison ; `Source.last_event_at` is always tz-aware (Django
    DateTimeField), but the `SourcePayload.occurred_at` contract isn't
    enforced anywhere, so a future connector returning a naive
    datetime would crash the comparison with `TypeError` and abort
    the source mid-poll. Normalizing at this seam keeps the watermark
    invariant downstream and the failure recoverable."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class _PolledSource:
    """Connector iterator wrapped so the caller can read `count` + `newest`
    occurred_at after `record_items` has consumed it.

    Keeps the poll loop streaming (no list materialization) while still
    surfacing the count + watermark the caller needs for progress + the
    per-source watermark advance.

    Failure semantics: ANY error while pulling the next payload propagates
    to the per-source handler ; the source counts as failed and its
    watermark stays put. No partial-success arm, for two reasons. A
    connector `poll()` is a generator, so the error CLOSES it ; nothing
    more can flow this cycle no matter what we do here. And advancing the
    watermark over a partial stream LOSES DATA on newest-first sources
    (Reddit /new): the prefix that flowed is the newest posts, so a
    watermark moved to its head strands every unreached older-but-new post
    below it, permanently. Keeping the watermark makes the failed cycle
    free: the next cycle re-reads from the same point and the external_id
    dedup absorbs anything that was already recorded. A CLEAN end with
    zero observed (a quiet source, nothing past the watermark) raises
    nothing and stays a success.
    """

    def __init__(self, payloads: Iterator[SourcePayload], *, initial_newest: datetime) -> None:
        self._payloads = payloads
        self.newest = _ensure_aware(initial_newest)
        self.observed = 0

    def __iter__(self) -> Iterator[SourcePayload]:
        for payload in self._payloads:
            self.observed += 1
            occurred_at = _ensure_aware(payload.occurred_at)
            if occurred_at > self.newest:
                self.newest = occurred_at
            yield payload


class FeedPollOperation:
    """One-shot: poll a single Feed's streams, persist + prune its items."""

    def __init__(
        self,
        feed: Feed,
        *,
        on_progress: FeedPollProgressCallback | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> None:
        config = load_config(feed)
        if not isinstance(config, CuratedFeedConfig):
            raise NotImplementedError(f"Unsupported feed kind: {feed.kind}")
        self.feed = feed
        self.config = config
        self.account_id = str(feed.account_id)
        self.on_progress: FeedPollProgressCallback = on_progress or (lambda _: None)
        # Called once per source to renew the poll lease (see poll_feed) ;
        # returns False if the lease was lost (another worker took over), in
        # which case we stop early. None = no lease to renew (direct call/test).
        self.heartbeat: Callable[[], bool] = heartbeat or (lambda: True)

    @cached_property
    def feed_svc(self) -> FeedService:
        return FeedService(account_id=self.account_id)

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.account_id)

    @cached_property
    def source_svc(self) -> "SourceService":
        # Local import to avoid the polling -> sources -> feeds.models cycle
        # at module load; the property fires once per operation lifetime.
        from .sources import SourceService

        return SourceService(account_id=self.account_id)

    def run(self) -> FeedPollResult:
        started_at = timezone.now()
        observed = 0
        recorded = 0
        sources_succeeded = 0

        # Counted up front (cheap COUNT, no row materialization) so the
        # early-break log + full-outage check have the total ; sources
        # themselves stream in RANDOM order (see `iter_for_poll`) so a
        # fixed position can't doom the same sources every cycle.
        total_sources = self.source_svc.count(self.feed)
        for source in self.source_svc.iter_for_poll(self.feed):
            # Renew the poll lease BEFORE the (potentially slow) fetch, so the
            # lock spans this source's full duration. Renewing per source is
            # what lets a feed of any size poll under one held lock instead of
            # racing the fixed TTL. If the lease was lost (another worker took
            # over after an over-long stall), stop: continuing is the redundant
            # double-poll the lock exists to prevent. Per-source watermarks are
            # already persisted, so stopping early loses no progress.
            if not self.heartbeat():
                logger.warning(
                    "feed=%s: poll lease lost mid-cycle after %d/%d sources; yielding to the new holder",
                    self.feed.id,
                    sources_succeeded,
                    total_sources,
                )
                break
            # Spec parse guarded so a malformed `source.spec` JSON
            # (shouldn't happen ; writes validate ; but the polling
            # contract is "one bad row never aborts the cycle") logs
            # and continues instead of skipping every later source.
            try:
                spec = _SPEC_ADAPTER.validate_python(source.spec)
            except ValidationError as exc:
                logger.warning(
                    "source polling failed feed=%s source=%s#%s err=ValidationError: %s",
                    self.feed.id,
                    source.kind,
                    source.id,
                    exc,
                )
                continue
            try:
                s_observed, s_recorded = self._poll_source(source, spec)
                observed += s_observed
                recorded += s_recorded
                sources_succeeded += 1
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "source polling failed feed=%s spec=%s err=%s: %s",
                    self.feed.id,
                    spec.display(),
                    type(exc).__name__,
                    exc,
                )

        # Persist poll cadence only ; Source row watermarks are written
        # back per-row inside `_poll_source`; feed.data is not touched.
        full_outage = total_sources > 0 and sources_succeeded == 0
        self.feed_svc.update_poll_state(self.feed, last_polled_at=started_at, data=None)

        # Skip prune on the same full-outage signal. A sustained connector
        # outage would otherwise erode the retention window with no fresh
        # data flowing in ; by day retention+1 the watch cursors would
        # be pointing at pruned ids with nothing to judge once it clears.
        if full_outage:
            logger.warning(
                "feed=%s: all %d sources failed this cycle; skipping retention prune",
                self.feed.id,
                total_sources,
            )
            pruned = 0
        else:
            # Plain retention prune. The subscriber-cursor floor (so a
            # lagging Watch can't lose items pruned from under it) returns
            # with WatchCursor.
            pruned = self.feed_item_svc.prune_items(
                self.feed,
                retention_days=self.config.retention_days,
                now=started_at,
            )
        return FeedPollResult(observed=observed, recorded=recorded, pruned=pruned)

    def _poll_source(self, source: Source, spec) -> tuple[int, int]:
        """Poll one source row. Returns (observed, recorded). Advances the
        row's watermark in place via a single UPDATE ; no JSONB rewrite.

        `source.last_event_at` is filled in by feeds.policy at save time
        (or carried by the data migration), so it's a real datetime by
        invariant. The connector treats it as a since-cursor; items with
        `occurred_at <= since` are skipped.

        The effective `field_map` is `default_field_map | row.field_map`
        (row wins per key) ; passed through to the connector so a single
        Feed can mix sources whose publishers put the body in different
        fields without rewriting the feed-level default. Connectors that
        don't read field_map accept and ignore it."""
        field_map = {**self.config.default_field_map, **(source.field_map or {})}
        connector = source_registry.get(spec.kind)
        # heartbeat: the lease tick the run loop fires between sources, ALSO
        # handed to the connector so a long intentional wait inside one
        # source (rate-limit backoff sleeps) keeps renewing the poll lease.
        payload_iter = _PolledSource(
            connector.poll(spec, since=source.last_event_at, field_map=field_map, heartbeat=self.heartbeat),
            initial_newest=source.last_event_at,
        )
        recorded = self.feed_item_svc.record_items(
            self.feed,
            source_label=spec.display(),
            source_meta=source.meta or {},
            payloads=payload_iter,
        )
        if payload_iter.newest != source.last_event_at:
            self.source_svc.advance_watermark(source, payload_iter.newest)
        self.on_progress(SourcePolled(self.feed, spec.display(), payload_iter.observed, recorded))
        return payload_iter.observed, recorded


def poll_feed(feed: Feed, *, on_progress: FeedPollProgressCallback | None = None) -> FeedPollResult | None:
    """Locked entry point for a single Feed's poll cycle. Returns None if
    another process holds `poll_lock(feed.id)` (caller records a skip)."""
    with poll_lock(str(feed.id)) as lease:
        if not lease:
            return None
        # Renew the lease as each source completes so the lock outlives a
        # large feed's full cycle (POLL_LOCK_TIMEOUT_SECONDS is the per-source
        # liveness window, not a total-time cap).
        return FeedPollOperation(feed, on_progress=on_progress, heartbeat=lease.renew).run()
