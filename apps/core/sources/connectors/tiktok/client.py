"""SerpBase TikTok search client (`POST https://api.serpbase.dev/tiktok/search`).

SerpBase is a paid HTTP API in front of TikTok's keyword search; this install
polls it with its own key (SERPBASE_API_KEY). The reply is an envelope whose
`status` is a BUSINESS code, so HTTP 200 alone is not success: a 200 carrying
`status: 1001` (bad key) or `1020` (no credits) has no `posts` at all, and
reading it as "no matches" would silently look like a quiet search.

Only the HTTP boundary lives here. A reply the API marks as failed, or a body of
the wrong shape, raises `ConnectorParseError`, which the poll loop handles as a
recoverable per-source error (log, skip, keep the watermark) instead of
aborting the feed's whole cycle.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from django.conf import settings

from ..base import (
    ConnectorParseError,
    openmagpie_user_agent,
    parse_rate_limit_wait,
    rate_limit_delay,
    read_response_capped,
    sleep_with_heartbeat,
)

logger = logging.getLogger("sources")

API_URL = "https://api.serpbase.dev/tiktok/search"
SERPBASE_API_KEY_SETTING = "SERPBASE_API_KEY"

REQUEST_TIMEOUT_SECONDS = 60.0
MAX_BODY_BYTES = 5 * 1024 * 1024

# The envelope's business status. 0 is success; the rest are reported on an HTTP
# 200 reply. 1029 (rate limited) and the 1500-1504 transient family are
# retryable; 1000 / 1001 / 1020 are not (fix the request / key / balance).
STATUS_SUCCESS = 0
STATUS_RATE_LIMITED = 1029
RETRYABLE_STATUSES = frozenset({STATUS_RATE_LIMITED, 1500, 1502, 1503, 1504})
_STATUS_DETAIL = {
    1000: "the request was rejected",
    1001: f"{SERPBASE_API_KEY_SETTING} is not valid",
    1003: "this product is not available on the plan",
    1004: "no such product endpoint",
    1020: "the SerpBase account is out of credits",
    1029: "rate limited",
}

# A rate limit (HTTP 429 or status 1029) or a transient upstream failure is
# retried in-cycle a couple of times, then the source fails for this cycle and
# the next scheduled poll retries from the same watermark. The wait is capped
# well under the poll lease (600s), so a real outage defers to the next poll
# rather than pinning a worker.
MAX_RETRIES = 2
RATE_LIMIT_BACKOFF_BASE_SECONDS = 5.0
RATE_LIMIT_DELAY_CAP_SECONDS = 60.0


@dataclass(frozen=True)
class SearchPage:
    posts: list[dict[str, Any]]
    next_cursor: str | None  # None when the reply reports no further page


class SerpBaseClient:
    """Reads the key from settings per request (so `override_settings` applies);
    a changed `.env` still needs the server to reload it."""

    def __init__(
        self, *, timeout: float = REQUEST_TIMEOUT_SECONDS, _transport: httpx.BaseTransport | None = None
    ) -> None:
        """`_transport` is a TEST-ONLY override for faking the API."""
        self._timeout = timeout
        self._transport = _transport

    def search(
        self,
        query: str,
        *,
        count: int,
        cursor: str | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> SearchPage:
        """One keyword search: at most `count` posts, newest relevance first."""
        body: dict[str, Any] = {"q": query, "count": count}
        if cursor:
            body["cursor"] = cursor
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": getattr(settings, SERPBASE_API_KEY_SETTING, "").strip(),
            "User-Agent": openmagpie_user_agent(),
        }
        if not headers["X-API-Key"]:
            # `required_settings` makes the poller refuse the source first; this guards
            # a direct call (a test or a script) from sending an unauthenticated request.
            raise ConnectorParseError(f"tiktok search needs {SERPBASE_API_KEY_SETTING} set")
        with httpx.Client(timeout=self._timeout, headers=headers, transport=self._transport) as client:
            envelope = self._post(client, body, heartbeat)
        return _page(envelope)

    def _post(
        self, client: httpx.Client, body: dict[str, Any], heartbeat: Callable[[], bool] | None
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            with client.stream("POST", API_URL, content=json.dumps(body)) as response:
                http_status = response.status_code
                envelope = (
                    _parse(read_response_capped(response, max_bytes=MAX_BODY_BYTES, url_label=API_URL))
                    if http_status < 400
                    else None
                )
                status = envelope.get("status") if envelope is not None else None
                retryable = http_status == 429 or http_status >= 500 or status in RETRYABLE_STATUSES
                if not retryable:
                    if envelope is None:
                        raise ConnectorParseError(f"tiktok search failed with HTTP {http_status}")
                    _require_success(envelope)
                    return envelope
                if attempt >= MAX_RETRIES:
                    raise ConnectorParseError(
                        f"tiktok search failed ({_reason(http_status, envelope)}); the next poll retries"
                    )
                wait = parse_rate_limit_wait(response)
            delay = rate_limit_delay(
                wait, attempt, base=RATE_LIMIT_BACKOFF_BASE_SECONDS, cap=RATE_LIMIT_DELAY_CAP_SECONDS
            )
            logger.info("tiktok search retrying in %.0fs (attempt %d/%d)", delay, attempt + 1, MAX_RETRIES)
            sleep_with_heartbeat(delay, heartbeat)
            attempt += 1


def _parse(raw: bytes) -> dict[str, Any]:
    """The reply as a JSON object (no status judgment yet)."""
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectorParseError(f"tiktok search returned a non-JSON body: {exc}") from exc
    if not isinstance(envelope, dict):
        raise ConnectorParseError("tiktok search returned a non-object body")
    return envelope


def _require_success(envelope: dict[str, Any]) -> None:
    """Raise unless the envelope's BUSINESS status is 0 (HTTP 200 is not enough)."""
    status = envelope.get("status")
    if status == STATUS_SUCCESS:
        return
    detail = _STATUS_DETAIL.get(status, "unexpected status")
    request_id = str(envelope.get("request_id") or "")
    suffix = f" (request_id {request_id})" if request_id else ""
    raise ConnectorParseError(f"tiktok search status {status}: {detail}{suffix}")


def _reason(http_status: int, envelope: dict[str, Any] | None) -> str:
    """A one-line why, for the give-up message."""
    if envelope is not None and envelope.get("status") != STATUS_SUCCESS:
        return f"status {envelope.get('status')}: {_STATUS_DETAIL.get(envelope.get('status'), 'unexpected')}"
    return f"HTTP {http_status}"


def _page(envelope: dict[str, Any]) -> SearchPage:
    """The posts and next cursor of a successful reply, shape-checked."""
    posts = envelope.get("posts")
    if not isinstance(posts, list):
        raise ConnectorParseError("tiktok search reply has no `posts` list (unexpected payload)")
    raw_pagination = envelope.get("pagination")
    pagination: dict[str, Any] = raw_pagination if isinstance(raw_pagination, dict) else {}
    cursor = pagination.get("next_cursor")
    has_more = bool(pagination.get("has_more")) and isinstance(cursor, str) and bool(cursor)
    return SearchPage(
        posts=[post for post in posts if isinstance(post, dict)],
        next_cursor=cursor if has_more else None,
    )
