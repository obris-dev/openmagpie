"""Facebook group search connector, unofficial route (Camofox).

Polls a `facebook_group` source: one live Facebook group search per cycle
via the facebook-worker.py subprocess (which drives a Camofox anti-detect
browser session), mapping each normalized post record to a
`NewFacebookPostPayload` newer than the source's `since` watermark.

Error semantics follow the connector contract: any Facebook/Camofox
failure is raised as `ConnectorParseError` (a `_RECOVERABLE_ERRORS`
member at the poll seam), so a bad source logs + skips instead of
aborting the feed cycle. The source's watermark stays put on failure, so
the next cycle re-reads from the same point and the external_id dedup
absorbs anything already recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime

from openmagpie_schema.configs import FacebookGroupSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError
from .client import FacebookClient
from .errors import FacebookError
from .payloads import NewFacebookPostPayload

log = logging.getLogger("sources.facebook")


class FacebookGroupConnector(BaseConnector[FacebookGroupSourceSpec]):
    """Polls one Facebook group search stream via the Camofox client.

    Live-mode semantics mirror the other connectors: every cycle yields
    posts newer than `since` (the Source row's `last_event_at`). There is
    no pagination in phase 1: a search returns up to `spec.count` posts
    and the connector filters them by the watermark. The worker session
    and cookies are resolved per search via the client's env/file/
    credentials-dir resolution (see client.load_cookies).
    """

    kind = FacebookGroupSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [NewFacebookPostPayload]

    # Client created lazily on first poll (avoids worker-path resolution at
    # import time, which fails when the sibling checkout isn't present).
    _client: FacebookClient | None = None

    @property
    def _resolved_client(self) -> FacebookClient:
        if self._client is None:
            self._client = FacebookClient()
        return self._client

    def poll(
        self,
        spec: FacebookGroupSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        del field_map
        del heartbeat
        client = self._resolved_client
        try:
            data = client.search_group(spec.group_ids, spec.terms, spec.count)
        except FacebookError as exc:
            log.warning(
                "facebook group search failed groups=%r code=%s retryable=%s: %s",
                spec.group_ids,
                exc.code,
                exc.retryable,
                exc.message,
            )
            raise ConnectorParseError(
                f"facebook group search {spec.display()} failed: {exc.code}: {exc.message} ({exc.action})"
            ) from exc

        results = data.get("result", {})
        for record in results.get("results", []):
            payload = NewFacebookPostPayload.from_record(record, query_terms=spec.terms)
            # Watermark filter: only surface posts strictly newer than the
            # cursor (the poll op advances the source watermark to the
            # newest seen, so a post at the watermark is already recorded).
            if since is not None and payload.occurred_at <= since:
                continue
            yield payload


register(FacebookGroupConnector.kind, FacebookGroupConnector.payloads)
