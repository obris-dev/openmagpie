"""Batch-poll a feed's reddit_subreddit sources via combined requests.

Instead of one `/r/<sub>/new.rss` request per sub, `RedditBatchMixin.poll_reddit_group`
STREAMS the feed's reddit sources, packs them into `/r/a+b+c/new.rss` multireddit
request(s) up to Reddit's URL limit, scatters each post back to its source, and
advances each watermark - one rate-limited call per ~1000-sub chunk instead of
one per sub.

A MIXIN folded into `FeedPollOperation` (not free functions): the orchestration
reaches into the operation's feed-side collaborators (`feed_item_svc`,
`source_svc`, `on_progress`, `heartbeat`, `feed`), so `self` carries them just as
it does for the per-source path - mirroring `WatchActionRunService`'s
`DigestBatchMixin`. Split into its own module only to keep `polling.py` under the
file-length cap. The two pure helpers (`_parse_reddit`, `_iter_url_budget_chunks`)
stay module-level functions - they take values, not the operation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from feeds.models import Source
from openmagpie_schema.configs import RedditSubredditSourceSpec, SourceSpec
from sources import registry as source_registry
from sources.connectors.reddit.connector import (
    COMBINED_URL_OVERHEAD,
    MAX_COMBINED_URL_BYTES,
    RedditSubRedditConnector,
)

from ._poll_common import _RECOVERABLE_ERRORS, SourcePolled, _ensure_aware

if TYPE_CHECKING:
    from feeds.models import Feed
    from sources.payloads import SourcePayload

    from ._poll_common import FeedPollProgressCallback
    from .feeds import FeedItemService
    from .sources import SourceService

logger = logging.getLogger("feeds")

# Local validator: a TypeAdapter is cheap, and defining it here (rather than
# importing polling's) keeps the polling <-> batch module pair acyclic.
_SPEC_ADAPTER = TypeAdapter(SourceSpec)

_RedditPair = tuple[Source, RedditSubredditSourceSpec]


class RedditBatchMixin:
    """Combined-request reddit polling, mixed into `FeedPollOperation`."""

    # Supplied by the host operation (FeedPollOperation); declared so the mixin's
    # methods type-check against them, like DigestBatchMixin's `account_id: str`.
    feed: Feed
    heartbeat: Callable[[], bool]
    on_progress: FeedPollProgressCallback
    feed_item_svc: FeedItemService
    source_svc: SourceService

    def poll_reddit_group(self, sources: Iterator[Source]) -> tuple[int, int, int]:
        """Poll a feed's reddit_subreddit sources via combined `/r/a+b+c/new.rss`
        request(s). Returns (observed, recorded, sources_succeeded).

        STREAMS `sources` (never materializes the whole set - a feed can carry
        1000+ of one kind) and packs them into chunks whose URL stays under
        Reddit's ~8KB wall, firing + recording each chunk as it fills - so neither
        the source rows nor the payloads of more than one chunk are ever held at
        once. Each chunk is an independent combined feed (see `_poll_chunk`). The
        lease is renewed before each chunk; if it's lost we stop (processed
        chunks' marks are persisted)."""
        # Resolve the connector ONCE, before the loop: it's loaded for every chunk
        # or none, so a (practically unreachable) mismatch raised here can't
        # discard earlier chunks' succeeded counts the way a per-chunk raise would.
        connector = source_registry.get(RedditSubredditSourceSpec.SOURCE_KIND)
        if not isinstance(connector, RedditSubRedditConnector):
            raise KeyError(RedditSubredditSourceSpec.SOURCE_KIND)
        observed = recorded = succeeded = 0
        for chunk in _iter_url_budget_chunks(_parse_reddit(self.feed.id, sources)):
            if not self.heartbeat():
                # Mirror the per-source loop's WARNING so an operator debugging
                # "why did reddit stop polling mid-cycle" sees the lease loss.
                logger.warning(
                    "reddit batch poll: lease lost mid-cycle feed=%s; yielding to the new holder", self.feed.id
                )
                break
            c_observed, c_recorded, c_succeeded = self._poll_chunk(connector, chunk)
            observed += c_observed
            recorded += c_recorded
            succeeded += c_succeeded
        return observed, recorded, succeeded

    def _poll_chunk(self, connector: RedditSubRedditConnector, chunk: list[_RedditPair]) -> tuple[int, int, int]:
        """One combined request for a budget-sized chunk: fetch from the EARLIEST
        mark in the chunk, scatter each post by its OWN subreddit tag (case-folded
        - the Atom term's casing can differ from the spec slug, and subs are
        case-insensitive), apply each sub's OWN watermark cutoff, record, and
        advance each source to ITS OWN newest post.

        The combined fetch takes ONE `since` (=min(marks)), so a sub ahead of that
        earliest mark gets older overlap in its bucket. The per-sub `>=` cutoff
        drops that overlap so the batch matches N single-source polls (accurate
        observed, no inserts the dedup would just reject, no backfill for a sub
        younger than a chunk-mate); the FeedItem unique constraint is then only a
        belt for the boundary tie + a cross-posted id appearing under two subs in
        one chunk.

        Per-sub advance (NOT the chunk's global newest) on purpose: the ~1000-item
        budget is shared across the chunk's subs, so a sub flooded past the cap can
        truncate the walk before a quiet sibling's older-but-new posts. Converging
        the quiet sub to the global newest would strand those permanently;
        advancing only to its own newest keeps its mark put so it re-reads next
        cycle. A fetch error is swallowed so one chunk's failure doesn't abort the
        others or lose earlier chunks; the chunk's marks stay put and retry next
        cycle."""
        # last_event_at is non-null by the feeds.policy invariant. Guard
        # defensively: fetch from the earliest PRESENT mark (a stray null must not
        # widen the whole chunk to a from-head walk + TypeError min()), and log if
        # the invariant is ever violated rather than failing silently.
        present = [m for s, _ in chunk if (m := s.last_event_at) is not None]
        if len(present) < len(chunk):
            logger.warning(
                "reddit batch feed=%s: %d/%d sources have a null watermark (policy invariant violated)",
                self.feed.id,
                len(chunk) - len(present),
                len(chunk),
            )
        since = min(present) if present else None
        buckets: dict[str, list[SourcePayload]] = {}
        try:
            for payload in connector.poll_combined(
                [spec.subreddit for _, spec in chunk], since=since, heartbeat=self.heartbeat
            ):
                buckets.setdefault(payload.subreddit.lower(), []).append(payload)
        except _RECOVERABLE_ERRORS as exc:
            logger.warning(
                "reddit chunk poll failed feed=%s subs=%d err=%s: %s",
                self.feed.id,
                len(chunk),
                type(exc).__name__,
                exc,
            )
            return 0, 0, 0

        observed = recorded = 0
        for source, spec in chunk:
            items = buckets.get(spec.subreddit.lower(), [])
            # Per-sub watermark cutoff (the combined since=min(marks) pulled in
            # older overlap for an ahead sub). `>=` not `>`: a genuinely-new post
            # sharing the exact watermark second must not be silently dropped - an
            # already-recorded boundary post is caught by the FeedItem constraint
            # instead. Null mark = no cutoff (cold-start keeps all, then bootstraps).
            if source.last_event_at is not None:
                items = [p for p in items if _ensure_aware(p.occurred_at) >= source.last_event_at]
            s_recorded = self.feed_item_svc.record_items(
                self.feed,
                source_label=spec.display(),
                source_meta=source.meta or {},
                payloads=iter(items),
            )
            observed += len(items)
            recorded += s_recorded
            self.on_progress(SourcePolled(self.feed, spec.display(), len(items), s_recorded))
            # Advance to THIS sub's own newest. `_ensure_aware` normalizes each
            # occurred_at like the single-source path (_PolledSource), so both
            # watermark-advance paths share the tz-awareness invariant. A sub with
            # no fetched posts (quiet, or starved by a flooded sibling sharing the
            # chunk budget) keeps its mark and re-reads next cycle. Monotonic
            # (`> mark`), so an ahead source never rewinds; a null mark (invariant
            # says it can't happen) bootstraps to first-seen via advance_watermark's
            # isnull branch, so it self-heals rather than re-fetching forever.
            sub_newest = max((_ensure_aware(p.occurred_at) for p in items), default=None)
            if sub_newest is not None and (source.last_event_at is None or sub_newest > source.last_event_at):
                self.source_svc.advance_watermark(source, sub_newest)

        # sources_succeeded credit = len(chunk): one successful combined request
        # polled EVERY sub in it, so all count as polled-without-error (the
        # per-source loop's `+1`, batched). It only feeds run()'s
        # `sources_succeeded == 0` full-outage check, and over-crediting can only
        # move further from 0 - it can never fabricate a false outage, at worst it
        # suppresses a marginal prune-skip.
        return observed, recorded, len(chunk)


def _parse_reddit(feed_id: str, sources: Iterator[Source]) -> Iterator[_RedditPair]:
    """Stream sources -> valid (source, RedditSubredditSourceSpec) pairs, skipping
    a row whose spec won't validate (logged) and any case-only-duplicate sub - a
    legacy row from before the slug validator lowercased; the first row covers it,
    so the sub isn't double-fetched or double-recorded into the same bucket."""
    seen: set[str] = set()
    for source in sources:
        try:
            spec = _SPEC_ADAPTER.validate_python(source.spec)
        except ValidationError as exc:
            logger.warning(
                "source polling failed feed=%s source=%s#%s err=ValidationError: %s",
                feed_id,
                source.kind,
                source.id,
                exc,
            )
            continue
        if not isinstance(spec, RedditSubredditSourceSpec):
            continue
        if spec.subreddit.lower() in seen:
            continue  # case-only duplicate; the first row already covers this sub
        seen.add(spec.subreddit.lower())
        yield source, spec


def _iter_url_budget_chunks(pairs: Iterator[_RedditPair]) -> Iterator[list[_RedditPair]]:
    """Stream pairs into chunks whose combined `/r/a+b+c/new/.rss` URL stays under
    MAX_COMBINED_URL_BYTES (Reddit 414s above ~8KB), yielding each chunk as it
    fills so the caller holds only one chunk's rows. A validated sub (its slug
    length is bounded by RedditSubredditSourceSpec, well under the budget) always
    fits, so a single pair is never split ; a realistic feed yields one chunk."""
    budget = MAX_COMBINED_URL_BYTES - COMBINED_URL_OVERHEAD
    cur: list[_RedditPair] = []
    used = 0
    for source, spec in pairs:
        add = len(spec.subreddit) + (1 if cur else 0)  # "+" separator after the first
        if cur and used + add > budget:
            yield cur
            cur, used = [], 0
        cur.append((source, spec))
        used += add
    if cur:
        yield cur
