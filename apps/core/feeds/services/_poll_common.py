"""Shared internals of the feed poll path.

Pulled out of `polling.py` so the per-source orchestrator (`polling.py`) and the
reddit batch mixin (`_reddit_batch.py`) can both import these without the two
modules forming an import cycle. Pure shape + small helpers only - no DB or
service access lives here.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError

from feeds.models import Feed
from sources.connectors.base import ConnectorParseError

# Per-source failures we expect and recover from (one bad source must not abort
# the whole feed cycle). Anything else is a bug and should propagate.
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
    ConnectorParseError,
    # `source_registry.get(spec.kind)` raises bare KeyError when the connector
    # for that kind isn't loaded in this deployment (e.g. a config-only spec
    # like `rss` whose connector ships later). Without this, the whole cycle
    # aborts on the first unregistered kind ; last_polled_at never advances and
    # the feed stays perpetually due.
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


def _ensure_aware(value: datetime) -> datetime:
    """Tag a naive datetime as UTC; pass through aware datetimes untouched.

    Defensive belt for the watermark comparisons (the single-source
    `_PolledSource.newest` and the batch's per-sub cutoff). `Source.last_event_at`
    is always tz-aware (Django DateTimeField), but the `SourcePayload.occurred_at`
    contract isn't enforced anywhere, so a future connector returning a naive
    datetime would crash the comparison with `TypeError` and abort the source
    mid-poll. Normalizing at this seam keeps the watermark invariant downstream
    and the failure recoverable."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
