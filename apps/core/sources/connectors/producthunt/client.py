"""The Product Hunt GraphQL API v2 client.

Owns the request: the operator's developer token, the `posts` query, the
streamed body cap, rate limits, and the response shape. A response of the wrong
shape raises ConnectorParseError so it fails one source, not the feed's cycle.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from django.conf import settings

from ..base import (
    ConnectorParseError,
    as_positive_float,
    openmagpie_user_agent,
    parse_rate_limit_wait,
    rate_limit_delay,
    read_response_capped,
    sleep_with_heartbeat,
)

logger = logging.getLogger("sources")

API_URL = "https://api.producthunt.com/v2/api/graphql"
PRODUCT_HUNT_TOKEN_SETTING = "PRODUCT_HUNT_TOKEN"

PAGE_SIZE = 20
MAX_BODY_BYTES = 5 * 1024 * 1024

# Product Hunt limits by query complexity per token over a 15-minute window and
# sends the seconds left in it as X-Rate-Limit-Reset. A reset longer than the cap
# fails the source at once (the next cycle retries from the same watermark)
# rather than holding the feed's poll lease through waits that can't succeed.
RATE_LIMIT_RESET_HEADER = "X-Rate-Limit-Reset"
MAX_RATE_LIMIT_RETRIES = 2
RATE_LIMIT_BACKOFF_BASE_SECONDS = 5.0
RATE_LIMIT_DELAY_CAP_SECONDS = 120.0

POSTS_QUERY = """
query Posts($first: Int!, $after: String, $postedAfter: DateTime, $postedBefore: DateTime, $topic: String) {
  posts(first: $first, after: $after, order: FEATURED_AT, featured: true,
        postedAfter: $postedAfter, postedBefore: $postedBefore, topic: $topic) {
    edges {
      node {
        id name tagline description url featuredAt
        makers { name username }
        topics(first: 5) { edges { node { name } } }
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
"""


@dataclass(frozen=True)
class Page:
    posts: list[dict[str, Any]]
    next_cursor: str | None  # None on the last page


class ProductHuntClient:
    """Reads the token from settings per request (so `override_settings` applies);
    a changed `.env` still needs the server to reload it."""

    def __init__(self, *, timeout: float = 15.0, _transport: httpx.BaseTransport | None = None) -> None:
        """`_transport` is a TEST-ONLY override for faking the API."""
        self._timeout = timeout
        self._transport = _transport

    def posts_page(
        self,
        *,
        posted_after: datetime,
        posted_before: datetime,
        topic: str | None,
        after: str | None,
        heartbeat: Callable[[], bool] | None,
    ) -> Page:
        """One page of front-page launches created in [posted_after, posted_before),
        newest first."""
        variables = {
            "first": PAGE_SIZE,
            "after": after,
            "topic": topic,
            "postedAfter": posted_after.isoformat(),
            "postedBefore": posted_before.isoformat(),
        }
        headers = {
            "Authorization": f"Bearer {getattr(settings, PRODUCT_HUNT_TOKEN_SETTING, '')}",
            "Accept": "application/json",
            "User-Agent": openmagpie_user_agent(),
        }
        with httpx.Client(timeout=self._timeout, headers=headers, transport=self._transport) as client:
            data = self._post(client, {"query": POSTS_QUERY, "variables": variables}, heartbeat)
        return _page(data)

    def _post(self, client: httpx.Client, body: dict[str, Any], heartbeat: Callable[[], bool] | None) -> dict[str, Any]:
        attempt = 0
        while True:
            with client.stream("POST", API_URL, json=body) as response:
                if response.status_code == 401:
                    raise ConnectorParseError(
                        f"producthunt: the API refused the token (401); check {PRODUCT_HUNT_TOKEN_SETTING}"
                    )
                if response.status_code != 429 or attempt >= MAX_RATE_LIMIT_RETRIES:
                    response.raise_for_status()
                    raw = read_response_capped(response, max_bytes=MAX_BODY_BYTES, url_label=API_URL)
                    break
                wait = _reset_seconds(response)
                if wait is not None and wait > RATE_LIMIT_DELAY_CAP_SECONDS:
                    raise ConnectorParseError(
                        f"producthunt: rate limited for {wait:.0f}s more; the next poll retries from the same point"
                    )
                delay = rate_limit_delay(
                    wait, attempt, base=RATE_LIMIT_BACKOFF_BASE_SECONDS, cap=RATE_LIMIT_DELAY_CAP_SECONDS
                )
            logger.info(
                "producthunt rate limited (429); retrying in %.0fs (attempt %d/%d)",
                delay,
                attempt + 1,
                MAX_RATE_LIMIT_RETRIES,
            )
            sleep_with_heartbeat(delay, heartbeat)
            attempt += 1
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConnectorParseError(f"producthunt: the API returned a non-JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ConnectorParseError("producthunt: the API returned a non-object body")
        return data


def _page(data: dict[str, Any]) -> Page:
    """The posts and next cursor from a `posts` response, shape-checked."""
    errors = data.get("errors")
    if errors:
        messages = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in errors)
        raise ConnectorParseError(f"producthunt: the API returned errors: {messages}")
    connection = (data.get("data") or {}).get("posts") if isinstance(data.get("data"), dict) else None
    if not isinstance(connection, dict) or not isinstance(connection.get("edges"), list):
        raise ConnectorParseError("producthunt: response missing data.posts.edges (unexpected payload)")
    posts = [edge.get("node") for edge in connection["edges"] if isinstance(edge, dict)]
    if not all(isinstance(post, dict) for post in posts):
        raise ConnectorParseError("producthunt: a posts edge carried no node object (unexpected payload)")
    info = connection.get("pageInfo")
    info = info if isinstance(info, dict) else {}
    cursor = info.get("endCursor")
    has_next = bool(info.get("hasNextPage")) and isinstance(cursor, str) and bool(cursor)
    return Page(posts=posts, next_cursor=cursor if has_next else None)


def _reset_seconds(response: httpx.Response) -> float | None:
    """Product Hunt's reset header (seconds left in the window), else the standard
    Retry-After / X-RateLimit-Reset headers."""
    return as_positive_float(response.headers.get(RATE_LIMIT_RESET_HEADER)) or parse_rate_limit_wait(response)
