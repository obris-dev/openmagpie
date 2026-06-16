import logging
import math
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from openmagpie_schema.configs import RedditSubredditSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError, read_response_capped
from .payloads import NewRedditPostPayload

logger = logging.getLogger("sources")

# Reddit's anonymous `.json` endpoint is gated by a TLS / HTTP-2 fingerprint
# check, only a real browser handshake gets through (cookies, browser-shaped
# UA, Referer, and `Sec-Fetch-*` headers all fail from Python). The `.rss`
# endpoint serves the same /new listing as Atom XML with no fingerprint check,
# so we use it as the anonymous transport. For richer payloads (score,
# comments, upvote_ratio) switch to authenticated PRAW against
# oauth.reddit.com with a registered Reddit app
# (https://www.reddit.com/prefs/apps).
REDDIT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Reddit's anonymous max page size is 100. Cap pagination at MAX_PAGES so a
# feed that's been silent for weeks doesn't fetch unbounded history on
# wake; with PAGE_SIZE=100, MAX_PAGES=10 covers the latest ~1000 posts.
PAGE_SIZE = 100
MAX_PAGES = 10

# Per-page body cap. Reddit's .rss is typically <100KB ; one corrupted
# / oversize response shouldn't chew RAM. Streamed + capped via
# `read_response_capped` so we never buffer past the cap. Higher than
# the RSS connector's 5MB ceiling because Reddit pages 100 posts each ;
# left generous for any rich Atom payloads.
MAX_BODY_BYTES = 5 * 1024 * 1024

# Reddit rate-limits the anonymous endpoint by IP and answers 429, usually
# with a Retry-After. That's "slow down", not "broken": retry the SAME page
# after the wait it asked for (exponential fallback when the header is
# absent or unparseable) instead of aborting the source's cycle, which
# would also roll straight into hammering the sibling subreddit sources.
# Exhausted retries fall through to `raise_for_status()`, the existing
# recoverable per-source error path. The cap keeps a hostile / buggy
# header from stalling a poll worker for minutes.
MAX_RATE_LIMIT_RETRIES = 6
RATE_LIMIT_BACKOFF_BASE_SECONDS = 2.0  # 2s..60s over the 6 retries when Retry-After is absent (6th clamps to the cap)
RATE_LIMIT_DELAY_CAP_SECONDS = 60.0

# Backoff sleeps tick the caller's `heartbeat` at this cadence so the poll
# lease renews DURING the wait, not just between sources (the lease detects
# dead holders; a deliberate wait is alive). Far inside the lease window
# (POLL_LOCK_TIMEOUT_SECONDS, 600s), so even a worst-case stack of full
# 60s waits never lets the lease lapse mid-source.
HEARTBEAT_SLEEP_CHUNK_SECONDS = 15.0


def _rate_limit_delay(retry_after: str | None, attempt: int) -> float:
    """Seconds to wait before retrying a 429'd page: the numeric Retry-After
    when Reddit sent one, else exponential in the attempt number. Retry-After's
    HTTP-date form parses as non-numeric and falls back to the exponential.

    The header value is accepted only when finite and positive. "nan" must be
    screened explicitly: float("nan") parses, every comparison against it is
    False (so a `<= 0` guard passes it and `min(nan, cap)` returns nan, since
    min keeps the first argument on a False comparison), and time.sleep(nan)
    then raises a ValueError that is OUTSIDE the polling orchestrator's
    recoverable set - aborting the feed's whole cycle, every cycle."""
    delay: float | None = None
    if retry_after:
        try:
            parsed = float(retry_after)
        except ValueError:
            parsed = None
        if parsed is not None and math.isfinite(parsed) and parsed > 0:
            delay = parsed
    if delay is None:
        delay = RATE_LIMIT_BACKOFF_BASE_SECONDS * (2**attempt)
    return min(delay, RATE_LIMIT_DELAY_CAP_SECONDS)


def _sleep_with_heartbeat(total: float, heartbeat: Callable[[], bool] | None) -> None:
    """Sleep `total` seconds, ticking `heartbeat` every chunk so the
    caller's poll lease renews through the wait. The return value is
    deliberately ignored (see the Connector.poll contract). No heartbeat
    (direct calls / tests) = one plain sleep."""
    if heartbeat is None:
        time.sleep(total)
        return
    remaining = total
    while remaining > 0:
        chunk = min(remaining, HEARTBEAT_SLEEP_CHUNK_SECONDS)
        time.sleep(chunk)
        remaining -= chunk
        heartbeat()


def _entry_published(entry: Any) -> datetime | None:
    """feedparser exposes Atom `<published>` as `published_parsed`
    struct_time ALREADY IN UTC. Read the year/month/day/hour/minute/
    second fields straight into the datetime constructor ;
    `time.mktime` would interpret the struct as local wall-clock and
    shift every timestamp by the host's UTC offset on any non-UTC
    deploy (Reddit's old custom-ET path used `datetime.fromisoformat`,
    which was correct ; this path must preserve that)."""
    parsed = entry.get("published_parsed")
    if isinstance(parsed, time.struct_time):
        return datetime(
            parsed.tm_year,
            parsed.tm_mon,
            parsed.tm_mday,
            parsed.tm_hour,
            parsed.tm_min,
            parsed.tm_sec,
            tzinfo=UTC,
        )
    return None


class RedditSubRedditConnector(BaseConnector[RedditSubredditSourceSpec]):
    """Polls a single subreddit's `/r/<slug>/new/.rss` Atom feed.

    Live-mode semantics: every cycle is "yield posts newer than `since`".
    `since` is the source's `last_event_at`, which feed-config policy
    initializes to wall-clock now at save time (see `feeds/policy.py`), so
    in production `since` is always non-None and the first poll just sees
    the few posts published since the source was created.

    The `since=None` path (no watermark) is a dev / test entry only, and
    walks up to `MAX_PAGES * PAGE_SIZE` posts from the head of /new
    (Reddit caps anonymous /new at ~1000 items total).

    There is no backfill. Future Reddit variants (user feed, search, comments,
    ...) get their own connector class + kind. If one of them grows a real
    "backfill N days" requirement, that's a separate feature with its own
    state machine (cursor + horizon + completion flag), not a cursor smuggled
    in here.
    """

    kind = RedditSubredditSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [NewRedditPostPayload]

    # `count` is the universal poll-walk default from BaseConnector: it
    # re-walks the page fetch (~10 GETs to /new.rss) discarding each
    # payload. Reddit has no cheaper exact-count path, so we don't
    # override it.

    def _get_page(
        self,
        client: httpx.Client,
        url: str,
        params: dict[str, str | int],
        heartbeat: Callable[[], bool] | None,
    ) -> bytes:
        """GET one /new.rss page, sleeping out 429s (see the rate-limit
        constants above) with the poll lease heartbeat ticking through each
        wait. Any other status raises via `raise_for_status`, and so does a
        429 once the retries are exhausted, landing both in the polling
        orchestrator's recoverable per-source path - which leaves the
        source's watermark UNTOUCHED, so nothing is lost to a throttled
        cycle ; the next cycle re-reads from the same watermark.

        The retry itself logs INFO: a honored rate limit is the system
        working, not a fault, and must not page anyone. The fault signal
        (retries exhausted) is the orchestrator's per-source WARNING, fed
        by the raise here."""
        attempt = 0
        while True:
            with client.stream("GET", url, params=params) as response:
                if response.status_code != 429 or attempt >= MAX_RATE_LIMIT_RETRIES:
                    response.raise_for_status()
                    return read_response_capped(response, max_bytes=MAX_BODY_BYTES, url_label=url)
                delay = _rate_limit_delay(response.headers.get("Retry-After"), attempt)
            # Sleep AFTER the `with` closes the 429 response, so the wait
            # never pins the streamed connection open.
            logger.info(
                "%s rate limited (429); retrying in %.0fs (attempt %d/%d)",
                url,
                delay,
                attempt + 1,
                MAX_RATE_LIMIT_RETRIES,
            )
            _sleep_with_heartbeat(delay, heartbeat)
            attempt += 1

    def poll(
        self,
        spec: RedditSubredditSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[NewRedditPostPayload]:
        # Reddit Atom carries fixed, non-overridable fields ; the
        # connector ignores `field_map` (the Connector contract
        # accepts it for the RSS variant + future per-source
        # overrides). Documented as a no-op rather than silently
        # dropped so a future Reddit field-map use case (e.g. body
        # vs title-only) lands here intentionally.
        del field_map
        subreddit = spec.subreddit
        if not subreddit:
            # ConnectorParseError, NOT ValueError: only _RECOVERABLE_ERRORS
            # degrade to a failed source at the polling seam. A bare
            # ValueError would abort the WHOLE feed cycle on one bad row
            # (skipping every later source and update_poll_state), exactly
            # what the per-source contract exists to prevent.
            raise ConnectorParseError(f"RedditSubredditSourceSpec missing subreddit: {spec}")

        # `/new` is sorted newest -> oldest. Reddit has no server-side `since`
        # filter; the early-return on strict `payload.occurred_at < since` works
        # only because of that ordering, once we see a post strictly older
        # than `since`, every remaining post on this page and every later
        # page is older too. The `after` cursor walks pages newest -> oldest
        # in the same order.
        url = f"https://www.reddit.com/r/{subreddit}/new/.rss"
        after: str | None = None

        # One Client across all pages: shares the connection pool, so
        # `?after=` pagination reuses the keep-alive instead of
        # handshaking per page. Reddit doesn't strictly need the body
        # cap (fixed host, bounded pages), but routing through the
        # shared `read_response_capped` puts the cap policy in one
        # place ; an unexpected oversize 200 from a CDN edge surfaces
        # as a parse error instead of an OOM.
        with httpx.Client(
            timeout=15.0,
            headers={"User-Agent": REDDIT_USER_AGENT},
        ) as client:
            for _ in range(MAX_PAGES):
                params: dict[str, str | int] = {"limit": PAGE_SIZE}
                if after:
                    params["after"] = after

                body = self._get_page(client, url, params, heartbeat)

                parsed = feedparser.parse(body)
                # Gate on `not version`: real feeds set `version`
                # ('atom10' for Reddit ; HTML pages come back as ''.
                # Reddit's anti-bot rate-limit / login page is a 200 with
                # HTML body ; without this gate it silently reads as "no
                # new posts" and never surfaces the block.
                #
                # Bozo is intentionally NOT a fail trigger. feedparser
                # raises bozo=1 with SAXParseException for non-fatal
                # quirks (undeclared namespace prefix, etc.) AND for hard
                # parse failures ; we can't reliably discriminate without
                # matching exception messages. The trade-off: a truncated
                # body that recovers 0 entries reads as "empty page" and
                # the loop returns ; next poll picks up when Reddit
                # recovers. The Reddit-specific concern (the .rss
                # endpoint being our anon channel) is fully covered by
                # the version gate alone.
                # `getattr(..., "")` because some inputs (empty body,
                # non-XML) cause feedparser to return a FeedParserDict
                # that raises AttributeError on `.version` access
                # instead of returning empty string.
                if not parsed.entries and not getattr(parsed, "version", ""):
                    raise ConnectorParseError(
                        f"reddit /r/{subreddit}/new/.rss returned an unexpected payload "
                        "(no feed format detected; likely the anti-bot HTML page)"
                    )

                if not parsed.entries:
                    return  # empty page, nothing more to consume

                last_atom_id: str | None = None
                for entry in parsed.entries:
                    published = _entry_published(entry)
                    if published is None:
                        # Reddit Atom always carries <published>; a missing one
                        # is a Reddit-side schema change. Skip the row instead
                        # of dropping the whole page (fail loud only on bozo
                        # + zero entries above).
                        continue
                    payload = NewRedditPostPayload.from_feedparser_entry(entry, spec, published)
                    last_atom_id = entry.get("id") or last_atom_id
                    # Strict `<`, not `<=`: two posts can share `<published>`
                    # to the second (batch import; same-second submissions),
                    # and dropping on tie permanently loses the second one
                    # because the watermark already crossed its second.
                    # The downstream `external_id` dedup on FeedItem is
                    # idempotent, so re-yielding the boundary post is
                    # suppressed at the recorder seam. The early-return
                    # remains safe: once we see a post strictly older than
                    # `since`, every remaining post on this and later pages
                    # is older too.
                    if since is not None and payload.occurred_at < since:
                        return
                    yield payload

                # Atom has no Reddit-style `after` cursor in the envelope, but
                # `?after=t3_xxx&limit=N` still works against `.rss`. Use the
                # last entry's thing-id (already `t3_<post-id>`) as the cursor.
                if not last_atom_id:
                    return  # nothing to page from
                after = last_atom_id


# Register payloads for hydration of FeedItem.data, single source of truth via the class attrs.
register(RedditSubRedditConnector.kind, RedditSubRedditConnector.payloads)
