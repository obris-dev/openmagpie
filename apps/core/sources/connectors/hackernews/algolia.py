"""The Hacker News Algolia search client.

Owns the "talk to the HN Algolia API and walk it" concern in one class:
the `search_by_date` endpoint, paging, the `since` watermark filter, the
`query` keyword pre-filter, and the streamed body cap. It is connector- and
spec-agnostic -- a caller passes the tag + filters + a `to_payload(hit, when)`
builder and gets back hydrated SourcePayloads. The Connector classes
(connector.py) hold an instance and adapt it to the Connector protocol;
future HN variants reuse it with their own tag + payload.
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx

from sources.payloads import SourcePayload

from ..base import ConnectorParseError, read_response_capped

logger = logging.getLogger("sources")

# `search_by_date` orders newest -> oldest, accepts a `created_at_i`
# numericFilter (the watermark, server-side), a full-text `query` (the keyword
# pre-filter), and returns fully-hydrated hits -- no per-item fan-out.
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

# Algolia caps a single query at 1000 hits regardless of `page`, so
# PAGE_SIZE * MAX_PAGES lands exactly on that ceiling. This is also the VOLUME
# GUARD: a poll pulls at most ~1000 of the NEWEST matching items. Caveat: if
# more than that many matching items arrive between polls, the older tail past
# the cap is never fetched and the advancing watermark skips it (silent loss).
# Unreachable for the documented uses (stories ~1k/day; tightly-keyworded
# comments); `walk` logs a warning when the cap is hit so a too-broad query
# surfaces. A lossless oldest-first paging scheme is possible but unwarranted.
PAGE_SIZE = 100
MAX_PAGES = 10

# Per-page body cap. A 100-hit page is typically a few hundred KB; stream +
# cap so a hostile / buggy oversize 200 surfaces as a parse error instead of
# buffering unbounded.
MAX_BODY_BYTES = 5 * 1024 * 1024


class AlgoliaSearch:
    """Client for the HN Algolia `search_by_date` endpoint.

    Stateless per call (a fresh httpx.Client per `walk`, so it's safe to share
    one instance across the connectors that hold it). Construction takes the
    paging/transport knobs; `walk` takes the per-poll query."""

    def __init__(
        self,
        *,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        max_body_bytes: int = MAX_BODY_BYTES,
        timeout: float = 15.0,
    ) -> None:
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_body_bytes = max_body_bytes
        self._timeout = timeout

    def walk(
        self,
        *,
        tag: str,
        since: datetime | None,
        query: str = "",
        match: str = "all",
        to_payload: Callable[[dict[str, Any], datetime], SourcePayload],
    ) -> Iterator[SourcePayload]:
        """Page `search_by_date` newest-first, yielding hydrated payloads.

        `to_payload(hit, occurred_at)` is the caller's builder (it binds its
        own spec/payload type). `query` is the server-side keyword pre-filter;
        `match="any"` turns its terms into an OR via Algolia `optionalWords`
        (Algolia has no inline OR operator -- listing words as optional makes
        any match qualify, though all-word matches still rank higher, so it is
        not a pure OR)."""
        params = self._params(tag=tag, since=since, query=query, match=match)
        with httpx.Client(timeout=self._timeout) as client:
            for page in range(self._max_pages):
                params["page"] = page
                hits: list[dict[str, Any]] = self._get_page(client, params)["hits"]
                if not hits:
                    return  # ran out of matching items

                for hit in hits:
                    created_at_i = hit.get("created_at_i")
                    if not isinstance(created_at_i, int | float):
                        # Every hit carries created_at_i; a missing one is a
                        # schema change. Skip the row, don't fail the page.
                        continue
                    try:
                        occurred_at = datetime.fromtimestamp(created_at_i, tz=UTC)
                    except (OverflowError, OSError, ValueError):
                        # A pathological epoch (out-of-range int) raises outside
                        # the orchestrator's recoverable set and would abort the
                        # whole feed cycle; skip the row like a missing ts.
                        continue
                    yield to_payload(hit, occurred_at)

                if len(hits) < self._page_size:
                    return  # short page == last page

            # Every page through the cap was full: Algolia still holds older
            # matching items we did not fetch, and the watermark will advance
            # past them (silent loss; see the PAGE_SIZE/MAX_PAGES note). Surface
            # it so a too-broad query gets noticed.
            logger.warning(
                "hackernews: hit the %d-item page cap (tags=%s query=%r); older matching "
                "items beyond the cap were not fetched this poll. Narrow the query if this recurs.",
                self._page_size * self._max_pages,
                tag,
                query,
            )

    def _params(self, *, tag: str, since: datetime | None, query: str, match: str) -> dict[str, Any]:
        params: dict[str, Any] = {"tags": tag, "hitsPerPage": self._page_size}
        if since is not None:
            # `>=`, not `>`: two items can share a `created_at_i` second and the
            # watermark sits on one. Including the boundary second re-yields the
            # already-recorded item (downstream external_id dedup suppresses it)
            # rather than dropping an unseen same-second sibling.
            params["numericFilters"] = f"created_at_i>={int(since.timestamp())}"
        query = (query or "").strip()
        if query:
            params["query"] = query
            if match == "any":
                params["optionalWords"] = query
        return params

    def _get_page(self, client: httpx.Client, params: dict[str, Any]) -> dict[str, Any]:
        """GET one page and return the parsed JSON object.

        A non-JSON / wrong-shape 200 becomes ConnectorParseError so it degrades
        to a failed source at the polling seam rather than aborting the feed
        cycle. Any non-2xx (incl. a 429 if we ever exceed Algolia's ~10k/hr/IP
        budget) raises via `raise_for_status`, the same recoverable path, which
        leaves the watermark untouched so the next cycle re-reads from it."""
        with client.stream("GET", SEARCH_URL, params=params) as response:
            response.raise_for_status()
            body = read_response_capped(response, max_bytes=self._max_body_bytes, url_label=SEARCH_URL)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ConnectorParseError(f"hackernews Algolia returned non-JSON body: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ConnectorParseError("hackernews Algolia response missing a `hits` array (unexpected payload)")
        return data
