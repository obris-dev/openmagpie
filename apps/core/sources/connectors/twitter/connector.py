"""X (Twitter) search connector, unofficial route (twikit).

Polls a `twitter_search` source: one live X search per cycle via the
twikit client (unclecode fork), mapping each result tweet to a
`NewTweetPayload` newer than the source's `since` watermark.

Error semantics follow the connector contract: any X/twikit failure is
raised as `ConnectorParseError` (a `_RECOVERABLE_ERRORS` member at the
poll seam), so a bad source logs + skips instead of aborting the feed
cycle. The source's watermark stays put on failure, so the next cycle
re-reads from the same point and the external_id dedup absorbs anything
already recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime

from openmagpie_schema.configs import TwitterSearchSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError
from .client import ListenerErrorWrapper, TwikitClient, TwikitProduct
from .payloads import NewTweetPayload

log = logging.getLogger("sources.twitter")

# Mode string twikit passes to X's search endpoint. The spec's `latest` /
# `top` literals map 1:1 to twikit's `"Latest"` / `"Top"`.
_TWIKIT_MODES: dict[str, TwikitProduct] = {"latest": "Latest", "top": "Top"}


class TwitterSearchConnector(BaseConnector[TwitterSearchSourceSpec]):
    """Polls one X (Twitter) search stream via the unofficial twikit route.

    Live-mode semantics mirror the other connectors: every cycle yields
    tweets newer than `since` (the Source row's `last_event_at`). There is
    no pagination in phase 1: a search returns up to `spec.count` tweets
    and the connector filters them by the watermark (X's own recency
    ordering makes the first page the newest; a quiet stream needs no
    backfill walk). Multi-account session rotation (listeningkit's
    session_pool) is a follow-up; phase 1 uses one cookie set via the
    client's env/file/credentials-dir resolution.
    """

    kind = TwitterSearchSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [NewTweetPayload]

    # One stateless client; cookies + proxy resolved per search from the
    # live env / credentials files (see client.load_cookies).
    _client = TwikitClient()

    def poll(
        self,
        spec: TwitterSearchSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        del field_map
        del heartbeat
        try:
            results = self._client.search(spec.query, _TWIKIT_MODES[spec.mode], spec.count)
        except ListenerErrorWrapper as exc:
            err = exc.error
            log.warning(
                "twitter search failed query=%r code=%s retryable=%s: %s",
                spec.query,
                err.code,
                err.retryable,
                err.message,
            )
            raise ConnectorParseError(
                f"twitter search {spec.display()} failed: {err.code}: {err.message} ({err.action})"
            ) from exc

        for tweet in results:
            payload = NewTweetPayload.from_tweet(tweet, query=spec.query)
            # Watermark filter: only surface tweets strictly newer than the
            # cursor (the poll op advances the source watermark to the
            # newest seen, so a tweet at the watermark is already recorded).
            if since is not None and payload.occurred_at <= since:
                continue
            if spec.lang and payload.lang and payload.lang != spec.lang:
                continue
            yield payload


register(TwitterSearchConnector.kind, TwitterSearchConnector.payloads)
