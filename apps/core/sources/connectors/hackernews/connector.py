from collections.abc import Callable, Iterator
from datetime import datetime

from openmagpie_schema.configs import HackerNewsCommentSourceSpec, HackerNewsFeedSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector
from .algolia import AlgoliaSearch
from .payloads import HackerNewsCommentPayload, HackerNewsFeedPayload

# Story-feed selector -> Algolia `tags`. Comments use the literal `comment`
# tag (see HackerNewsCommentConnector). The ranked top/best feeds have no
# Algolia equivalent and are out of scope (see HackerNewsFeedSourceSpec).
_FEED_TAGS = {"new": "story", "show": "show_hn", "ask": "ask_hn"}
_COMMENT_TAG = "comment"

# Scope the keyword `query` to CONTENT, not the author. The HN Algolia index
# searches author + title + body, so query="users" would otherwise also match
# items BY a user named "users". Restrict to what the user means: a comment's
# body, or a story's title / url / text.
_FEED_SEARCHABLE = ["title", "url", "story_text"]
_COMMENT_SEARCHABLE = ["comment_text"]


class HackerNewsFeedConnector(BaseConnector[HackerNewsFeedSourceSpec]):
    """Polls one Hacker News story feed (new / Show HN / Ask HN) via Algolia.

    Live-mode semantics mirror the Reddit connector: every cycle yields posts
    newer than `since` (the source's `last_event_at`), which `AlgoliaSearch`
    pushes into the query as a `created_at_i>=` numericFilter so the server
    bounds it. The `since=None` path (dev/test) walks from the head of the feed
    up to the search client's page cap.

    `count` is the universal poll-walk default from BaseConnector (a handful of
    cheap JSON GETs); Algolia's `nbHits` can exceed what `poll` yields past the
    1000-hit cap, so using it directly would make count/poll disagree. Note that
    count re-walks `poll`, so wiring it into a warm cycle would re-fetch and
    re-emit walk's page-cap warning; nothing calls the connector's count() on the
    poll path today.
    """

    kind = HackerNewsFeedSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [HackerNewsFeedPayload]

    # Shared transport; stateless per call, so one instance is fine.
    _search = AlgoliaSearch()

    def poll(
        self,
        spec: HackerNewsFeedSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        # Fixed Algolia fields, no long waits: field_map and heartbeat are
        # accepted no-ops per the Connector contract.
        del field_map
        del heartbeat
        yield from self._search.walk(
            tag=_FEED_TAGS[spec.feed],
            since=since,
            query=spec.query,
            match=spec.match,
            restrict=_FEED_SEARCHABLE,
            to_payload=lambda hit, when: HackerNewsFeedPayload.from_algolia_hit(hit, spec, when),
        )


class HackerNewsCommentConnector(BaseConnector[HackerNewsCommentSourceSpec]):
    """Polls the Hacker News comment stream (`tags=comment`) via Algolia,
    narrowed by the spec's REQUIRED `query` keyword.

    `tags=comment` unfiltered is the site-wide firehose (~20k/day on average); the spec
    makes `query` required so a poll always carries a server-side keyword
    pre-filter before any per-item LLM cost, and the search client's page cap
    bounds the volume behind it. Same paged walk as the feed connector via the
    shared `AlgoliaSearch`; only the tag and payload differ.
    """

    kind = HackerNewsCommentSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [HackerNewsCommentPayload]

    _search = AlgoliaSearch()

    def poll(
        self,
        spec: HackerNewsCommentSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        del field_map
        del heartbeat
        yield from self._search.walk(
            tag=_COMMENT_TAG,
            since=since,
            query=spec.query,
            match=spec.match,
            restrict=_COMMENT_SEARCHABLE,
            to_payload=lambda hit, when: HackerNewsCommentPayload.from_algolia_hit(hit, spec, when),
        )


# Register payloads for hydration of FeedItem.data, single source of truth via the class attrs.
register(HackerNewsFeedConnector.kind, HackerNewsFeedConnector.payloads)
register(HackerNewsCommentConnector.kind, HackerNewsCommentConnector.payloads)
