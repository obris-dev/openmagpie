"""TikTok keyword-search connector, backed by SerpBase.

Polls a `tiktok_search` source: one keyword search per cycle through the
SerpBase `/tiktok/search` client, mapping each result video to a
`NewTikTokVideoPayload` newer than the source's `since` watermark.

Error semantics follow the connector contract: any HTTP / envelope failure is
raised as `ConnectorParseError` (a `_RECOVERABLE_ERRORS` member at the poll
seam), so a bad source logs + skips instead of aborting the feed cycle. The
watermark stays put on failure, so the next cycle re-reads from the same point
and the external_id dedup absorbs anything already recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import ClassVar

from openmagpie_schema.configs import TikTokSearchSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError
from .client import SERPBASE_API_KEY_SETTING, SerpBaseClient
from .payloads import NewTikTokVideoPayload, parse_timestamp

log = logging.getLogger("sources.tiktok")


class TikTokSearchConnector(BaseConnector[TikTokSearchSourceSpec]):
    """Polls one TikTok keyword search through SerpBase.

    Live-mode semantics mirror the other search connectors: every cycle yields
    videos newer than `since` (the Source row's `last_event_at`). There is no
    pagination in this first version: one search returns up to `spec.count`
    results and the connector filters them by the watermark, which the
    external_id dedup then absorbs. TikTok search returns relevance-ordered
    results, not a strict time series, so the watermark filter (not a cursor) is
    what keeps a poll incremental.
    """

    kind = TikTokSearchSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [NewTikTokVideoPayload]
    required_settings: ClassVar[dict[str, str]] = {
        SERPBASE_API_KEY_SETTING: "your SerpBase API key, from https://serpbase.dev/api-keys",
    }

    _client = SerpBaseClient()

    def poll(
        self,
        spec: TikTokSearchSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        del field_map  # the poller checks required_settings before calling poll
        page = self._client.search(spec.query, count=spec.count, heartbeat=heartbeat)

        for post in page.posts:
            occurred_at = parse_timestamp(post.get("published_at"))
            if occurred_at is None:
                # A result without a usable timestamp cannot be placed against the
                # watermark; skipping it beats inventing a time (which would either
                # replay it every cycle or hide it forever).
                log.warning("tiktok search: skipping post %r with no parsed published_at", post.get("id"))
                continue
            if since is not None and occurred_at <= since:
                # The boundary (`== since`) is skipped rather than re-offered: unlike
                # YouTube's day-granular upload_date, TikTok publishes second-resolution
                # timestamps, so a re-offered boundary item buys nothing.
                continue
            try:
                yield NewTikTokVideoPayload.from_post(post, spec, occurred_at=occurred_at)
            except (AttributeError, TypeError, ValueError) as exc:
                raise ConnectorParseError(f"tiktok search: could not build a payload from a post: {exc}") from exc


register(TikTokSearchConnector.kind, TikTokSearchConnector.payloads)
