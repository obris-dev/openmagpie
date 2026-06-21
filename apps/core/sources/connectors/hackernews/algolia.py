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
from pydantic import ValidationError

from sources.payloads import SourcePayload

from ..base import ConnectorParseError, read_response_capped

logger = logging.getLogger("sources")

# `search_by_date` orders newest -> oldest, accepts a `created_at_i`
# numericFilter (the watermark, server-side), a full-text `query` (the keyword
# pre-filter), and returns fully-hydrated hits -- no per-item fan-out.
SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"

# PAGE_SIZE * MAX_PAGES bounds how many of the NEWEST matching items one poll
# fetches. This is a per-poll WORK cap, not an API ceiling: keyset paging issues
# a fresh `created_at_i <= cursor` query per page (always page 0), so the cursor
# walks past Algolia's 1000-hit-per-query pagination limit freely (verified). The
# walk drains the entire [since, newest] window when it fits under the cap, so
# catch-up after the poller falls behind is lossless up to PAGE_SIZE * MAX_PAGES
# items. Only a single-poll backlog larger than THAT gaps: the walk stops, the
# watermark advances to the newest yielded, and the older tail is skipped
# (newest-first paging with a forward-only watermark can't revisit it). Sized
# generously so realistic outage backlogs drain in one poll; `walk` logs an
# error if the cap is ever hit (narrow the query, or raise the cap).
PAGE_SIZE = 100
MAX_PAGES = 50  # 5000 items/poll

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
        restrict: list[str] | None = None,
        to_payload: Callable[[dict[str, Any], datetime], SourcePayload],
    ) -> Iterator[SourcePayload]:
        """Page `search_by_date` newest-first, yielding hydrated payloads.

        `to_payload(hit, occurred_at)` is the caller's builder (it binds its
        own spec/payload type). `query` is the server-side keyword pre-filter;
        `match="any"` turns its terms into an OR via Algolia `optionalWords`
        (Algolia has no inline OR operator -- listing words as optional makes
        any match qualify, though all-word matches still rank higher, so it is
        not a pure OR). `restrict` limits the keyword to those Algolia searchable
        attributes (e.g. the comment body), so it doesn't also match on the author
        or title; None searches every attribute (the index default)."""
        # Keyset paging, NOT page=N offsets: search_by_date is newest-first over a
        # LIVE feed, so an offset window shifts as items arrive mid-walk and a
        # boundary item can be skipped (then lost as the watermark advances).
        # Instead, anchor each next page on the oldest created_at_i seen so far
        # (created_at_i <= cursor) and walk older; the watermark is timestamp-based,
        # immune to insertions. The boundary second is re-fetched (downstream
        # external_id dedup drops the repeats); at HN volumes a single second never
        # fills a page, so the cursor always advances. (If one ever did, `<=` can't
        # advance past that second -- the page re-requests identically until
        # MAX_PAGES, then the cap warning fires; bounded and deduped, never a hang.)
        cursor: int | None = None
        with httpx.Client(timeout=self._timeout) as client:
            for _ in range(self._max_pages):
                params = self._params(tag=tag, since=since, query=query, match=match, before=cursor, restrict=restrict)
                hits: list[dict[str, Any]] = self._get_page(client, params)["hits"]
                if not hits:
                    return  # ran out of matching items

                oldest: int | None = None
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
                    try:
                        payload = to_payload(hit, occurred_at)
                    except ValidationError as exc:
                        # An Algolia field-shape drift raises pydantic's
                        # ValidationError -- outside the orchestrator's recoverable
                        # set, so it would abort the whole feed cycle. Re-raise as
                        # ConnectorParseError so it degrades THIS source instead.
                        raise ConnectorParseError(f"hackernews: could not build a payload from a hit: {exc}") from exc
                    yield payload
                    ts = int(created_at_i)
                    oldest = ts if oldest is None else min(oldest, ts)

                if len(hits) < self._page_size:
                    return  # short page == last page
                if oldest is None:
                    # A full page where not one hit carried a usable created_at_i:
                    # can't advance the cursor, so stop. Effectively total schema
                    # breakage (every HN hit has created_at_i); warn so it surfaces
                    # rather than silently truncating the walk, mirroring the cap branch.
                    logger.error(
                        "hackernews: a full page carried no usable created_at_i (tags=%s query=%r); "
                        "stopping the walk early. Likely an Algolia schema change.",
                        tag,
                        query,
                    )
                    return
                cursor = oldest  # keyset advance: next page is at-or-older than this

            # Every page through the cap was full: Algolia still holds older
            # matching items we did not fetch, and the watermark will advance
            # past them (silent loss; see the PAGE_SIZE/MAX_PAGES note). Surface
            # it so a too-broad query gets noticed. Fires once per walk; since
            # BaseConnector.count() re-walks poll(), wiring connector.count() into
            # the warm poll path would double-fire this -- dedupe then. Moot today:
            # nothing calls connector.count().
            logger.error(
                "hackernews: hit the %d-item page cap (tags=%s query=%r); older matching "
                "items beyond the cap were not fetched this poll. Narrow the query if this recurs.",
                self._page_size * self._max_pages,
                tag,
                query,
            )

    def _params(
        self,
        *,
        tag: str,
        since: datetime | None,
        query: str,
        match: str,
        before: int | None = None,
        restrict: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"tags": tag, "hitsPerPage": self._page_size}
        numeric: list[str] = []
        if since is not None:
            # `>=`, not `>`: two items can share a `created_at_i` second and the
            # watermark sits on one. Including the boundary second re-yields the
            # already-recorded item (downstream external_id dedup suppresses it)
            # rather than dropping an unseen same-second sibling.
            numeric.append(f"created_at_i>={int(since.timestamp())}")
        if before is not None:
            # Keyset cursor (see walk): at-or-older than the oldest item seen so
            # far. `<=` not `<` keeps same-second siblings that straddled the page
            # boundary; dedup drops the re-fetched ones.
            numeric.append(f"created_at_i<={before}")
        if numeric:
            params["numericFilters"] = ",".join(numeric)  # HN Algolia ANDs comma-separated filters
        query = (query or "").strip()
        if query:
            params["query"] = query
            # Own the index setting the documented `-word` exclude / "phrase"
            # operators rely on, instead of depending on the HN index default.
            params["advancedSyntax"] = "true"
            if restrict:
                # Scope the keyword to CONTENT, not author: the HN index searches
                # author/title/body, so query="users" would match comments BY a
                # user named "users" without this.
                params["restrictSearchableAttributes"] = ",".join(restrict)
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
