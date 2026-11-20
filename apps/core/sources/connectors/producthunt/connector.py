"""Product Hunt launches connector (the official GraphQL API v2).

Each install polls with its own developer token (PRODUCT_HUNT_TOKEN), within
Product Hunt's API terms of service. The connector never scrapes the site and
never reads the Atom feed, which is ranked rather than time ordered.
"""

import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from django.utils import timezone
from pydantic import ValidationError

from openmagpie_schema.configs import ProductHuntLaunchesSourceSpec
from sources.payload_registry import register
from sources.payloads import SourcePayload

from ..base import BaseConnector, ConnectorParseError
from .client import PRODUCT_HUNT_TOKEN_SETTING, ProductHuntClient
from .payloads import NewProductHuntLaunchPayload, parse_timestamp

logger = logging.getLogger("sources")

# A day's launches share one slot a minute either side of local midnight
# (07:0x or 08:0x UTC, with DST). Reading this far behind the watermark re-reads
# the watermark's whole day, so a launch added to it, or a day cut off mid-poll,
# is read in full next time; the item store's external_id dedup drops repeats.
SLOT_SPAN = timedelta(hours=1)
DAY = timedelta(days=1)

# One poll's share of the token's 15-minute budget (each page costs ~100 of 6250).
PAGES_PER_POLL = 30


class ProductHuntLaunchesConnector(BaseConnector[ProductHuntLaunchesSourceSpec]):
    """Front-page launches, read oldest day first so the watermark only ever lands
    on days read in full. A backlog bigger than one poll's budget continues on the
    next poll."""

    kind = ProductHuntLaunchesSourceSpec.SOURCE_KIND
    payloads: list[type[SourcePayload]] = [NewProductHuntLaunchPayload]
    required_settings: ClassVar[dict[str, str]] = {
        PRODUCT_HUNT_TOKEN_SETTING: "your Product Hunt developer token, "
        "from https://www.producthunt.com/v2/oauth/applications",
    }

    _client = ProductHuntClient()

    def poll(
        self,
        spec: ProductHuntLaunchesSourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        del field_map  # the poller checks required_settings before calling poll
        now = timezone.now()
        start = (since or now) - SLOT_SPAN
        budget = PageBudget(PAGES_PER_POLL)

        for day in utc_days(start, until=now):
            for post in self._launches_on(day, spec, budget, heartbeat):
                occurred_at = parse_timestamp(post.get("featuredAt"))
                if occurred_at is not None and occurred_at >= start:
                    yield _payload(post, spec, occurred_at)
            if budget.spent and day + DAY < now:
                logger.warning(
                    "producthunt: %s read through %s; %d day(s) left, continuing next poll",
                    spec.display(),
                    day.date(),
                    (now - day).days,
                )
                return

    def _launches_on(
        self,
        day: datetime,
        spec: ProductHuntLaunchesSourceSpec,
        budget: "PageBudget",
        heartbeat: Callable[[], bool] | None,
    ) -> Iterator[dict[str, Any]]:
        """Every front-page launch on one UTC day, page by page, while budget lasts."""
        cursor = None
        while budget.take():
            page = self._client.posts_page(
                posted_after=day,
                posted_before=day + DAY,
                topic=spec.topic or None,
                after=cursor,
                heartbeat=heartbeat,
            )
            yield from page.posts
            cursor = page.next_cursor
            if cursor is None:
                return


def utc_days(start: datetime, *, until: datetime) -> Iterator[datetime]:
    """Midnight UTC of each day from `start`'s day up to `until`."""
    day = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    while day < until:
        yield day
        day += DAY


@dataclass
class PageBudget:
    left: int

    def take(self) -> bool:
        if self.left == 0:
            return False
        self.left -= 1
        return True

    @property
    def spent(self) -> bool:
        return self.left == 0


def _payload(
    post: dict[str, Any], spec: ProductHuntLaunchesSourceSpec, occurred_at: datetime
) -> NewProductHuntLaunchPayload:
    try:
        return NewProductHuntLaunchPayload.from_post(post, spec, occurred_at=occurred_at)
    except (ValidationError, AttributeError, TypeError) as exc:
        raise ConnectorParseError(f"producthunt: could not build a payload from a post: {exc}") from exc


register(ProductHuntLaunchesConnector.kind, ProductHuntLaunchesConnector.payloads)
