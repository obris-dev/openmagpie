"""Error taxonomy for the X (Twitter) connector, ported from listeningkit.

Every call into twikit can raise a ``TwitterException`` subclass (or a
bootstrap failure when X serves a degraded shell). This module maps those
to a canonical ``TwitterError``; the connector translates that into
``ConnectorParseError`` at the poll boundary so the feed poll op recovers
per-source (one bad source must not abort the feed cycle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from twikit.errors import (
    AccountLocked,
    AccountSuspended,
    BadRequest,
    DuplicateTweet,
    Forbidden,
    InvalidMedia,
    NotFound,
    RequestTimeout,
    ServerError,
    TooManyRequests,
    TweetNotAvailable,
    TwitterException,
    Unauthorized,
    UserNotFound,
    UserUnavailable,
)

# The one error code the connector branches on (the rate-limit retry loop keys
# off it). Named so the check and the mapping below can't drift to a typo that
# silently disables retries (AGENTS.md: no bare state literals in status checks).
RATE_LIMITED = "rate_limited"

TWIKIT_ERROR_CODE: dict[type[TwitterException], str] = {
    BadRequest: "bad_request",
    Unauthorized: "unauthorized",
    Forbidden: "forbidden",
    NotFound: "not_found",
    RequestTimeout: "timeout",
    TooManyRequests: RATE_LIMITED,
    ServerError: "upstream_error",
    AccountSuspended: "account_suspended",
    AccountLocked: "account_locked",
    DuplicateTweet: "duplicate_tweet",
    TweetNotAvailable: "tweet_unavailable",
    InvalidMedia: "invalid_media",
    UserNotFound: "user_not_found",
    UserUnavailable: "user_unavailable",
}


@dataclass
class TwitterError:
    """Canonical error shape for one X fetch failure."""

    code: str  # stable machine code, see TWIKIT_ERROR_CODE
    message: str  # human-readable
    retryable: bool  # safe to retry with backoff?
    action: str  # what the ops layer should do
    context: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] | None = None
    rate_limit_reset: int | None = None  # unix ts from x-rate-limit-reset


BOOTSTRAP_BLOCKED_MARKERS = (
    "Couldn't get KEY_BYTE indices",
    "Couldn't get key from the page source",
)


def map_bootstrap_failure(exc: Exception, context: dict[str, Any] | None = None) -> TwitterError:
    """X served a degraded shell (bot wall) so twikit could not bootstrap its
    ClientTransaction. Cause is almost always egress IP reputation; fix =
    residential proxy (see listeningkit docs: proxy.md)."""
    msg = str(exc)
    code = "bootstrap_blocked" if any(m in msg for m in BOOTSTRAP_BLOCKED_MARKERS) else "internal"
    action = (
        "X served a degraded shell to this egress IP (no ondemand.s chunk map). "
        "Use a residential proxy + browser fingerprint."
        if code == "bootstrap_blocked"
        else "unknown failure; log raw and retry with backoff"
    )
    return TwitterError(
        code=code,
        message=msg,
        retryable=code == "internal",
        action=action,
        context=context or {},
    )


def map_twikit_error(exc: TwitterException, context: dict[str, Any] | None = None) -> TwitterError:
    """Translate a twikit exception into a canonical TwitterError."""
    code = TWIKIT_ERROR_CODE.get(type(exc), "twitter_error")
    reset = getattr(exc, "rate_limit_reset", None)
    headers = getattr(exc, "headers", None)

    # X's SearchTimeline intermittently 404s with an EMPTY body (observed
    # repeatedly on live polls: same query succeeds on retry seconds later,
    # independent of session/cookies/query). That is a transient upstream
    # flake on the search endpoint, NOT a deleted tweet/user: a genuine
    # not_found carries a message. Retryable so the ops layer backs off
    # instead of treating the source as dead.
    #
    # twikit 2.3.3 renders every HTTP error as `status: <code>, message:
    # "<body>"` (client/client.py and guest/client.py), so str(exc) is NEVER
    # empty, not even for an empty body. Detect the empty BODY via its
    # rendering instead: an empty body produces the trailing payload
    # `message: ""`; any other rendering carried response text, so the 404
    # is real.
    if isinstance(exc, NotFound) and str(exc).rstrip().endswith('message: ""'):
        return TwitterError(
            code="search_timeline_unavailable",
            message="X SearchTimeline returned an empty 404 (transient upstream flake)",
            retryable=True,
            action="retry with backoff; watermark stays put so the next cycle re-reads",
            context=context or {},
            headers=headers,
            rate_limit_reset=reset,
        )

    retryable_actions: dict[str, tuple[bool, str]] = {
        "bad_request": (False, "fix query / payload; do not retry as-is"),
        "unauthorized": (False, "refresh session (guest token / cookies) and retry once"),
        "forbidden": (False, "rotate session + proxy pin; alert"),
        "not_found": (False, "tweet/user no longer exists; skip"),
        "timeout": (True, "retry with backoff"),
        RATE_LIMITED: (True, f"backoff until reset ({reset})"),
        "upstream_error": (True, "retry with backoff; alert after 5 consecutive"),
        "account_suspended": (False, "pause account mode; rotate to a different session; alert"),
        "account_locked": (False, "Arkose challenge; pause account mode; alert"),
        "duplicate_tweet": (False, "skip (dedupe by design)"),
        "tweet_unavailable": (False, "skip"),
        "invalid_media": (False, "skip"),
        "user_not_found": (False, "skip"),
        "user_unavailable": (False, "skip"),
        "twitter_error": (True, "unknown upstream error; log raw and retry with backoff"),
    }
    retryable, action = retryable_actions.get(code, (True, "unknown; log and retry with backoff"))

    return TwitterError(
        code=code,
        message=str(exc),
        retryable=retryable,
        action=action,
        context=context or {},
        headers=headers,
        rate_limit_reset=reset,
    )
