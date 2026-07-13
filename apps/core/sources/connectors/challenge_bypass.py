"""Anti-bot challenge-bypass capability for connectors.

Mix `ChallengeBypassMixin` into any connector that needs to fetch URLs
behind Cloudflare / Imperva / DDoS-Guard JS challenges. The mixin
delegates to a FlareSolverr sidecar (headless Chrome in its own
container) which solves the challenge and returns the real response
body.

Today's only consumer is the RSS connector, which uses it as a
fallback when its normal httpx fetch returns a non-feed body. The
mixin is generic so a future connector facing the same class of block
can opt in by inheritance, without copying the sidecar plumbing.

Sidecar address comes from `SOURCE_CHALLENGE_BYPASS_URL`. Empty string
disables the capability ; the mixin returns None and the caller logs +
skips."""

import json
import logging

import httpx
from django.conf import settings

from .base import FETCH_DEFAULT_MAX_BYTES

logger = logging.getLogger("sources")  # bare app-name logger, like base.py / rss/connector.py


class ChallengeBypassMixin:
    """Stateless today ; the mixin shape leaves room for per-instance
    state (pooled httpx client, FlareSolverr session, retry policy)
    without rewriting callers."""

    def challenge_bypass_fetch(
        self,
        url: str,
        timeout_s: float = 90.0,
        max_bytes: int = FETCH_DEFAULT_MAX_BYTES,
    ) -> bytes | None:
        """Instance-method shim over `challenge_bypass_fetch` (below); see it for the
        contract. Kept so connector consumers keep calling `self.challenge_bypass_fetch`."""
        return challenge_bypass_fetch(url, timeout_s=timeout_s, max_bytes=max_bytes)


def challenge_bypass_fetch(
    url: str,
    *,
    timeout_s: float = 90.0,
    max_bytes: int = FETCH_DEFAULT_MAX_BYTES,
) -> bytes | None:
    """POST `url` to the FlareSolverr sidecar and return the
    response body bytes. Returns None when:
      - `SOURCE_CHALLENGE_BYPASS_URL` is empty (capability disabled)
      - sidecar is unreachable
      - sidecar returns a non-`ok` status or no solution
      - the recovered body exceeds `max_bytes`

    `max_bytes` mirrors each caller's own body cap so a
    challenge-protected source can't smuggle an arbitrarily large
    payload past the streaming cap that protects the normal fetch
    path. This buffers the JSON envelope in memory once
    (httpx.post is not streamed) ; full streaming-mid-response is a
    follow-up if we ever see the cap fire under load.

    SECURITY: the sidecar does its OWN egress with a real browser, so the
    pinned-IP SSRF guard that protects the direct fetch path CANNOT apply here.
    Callers passing an UNTRUSTED `url` (e.g. an item's external_url in article
    enrichment) MUST pre-validate the host themselves (block private-resolving
    hosts) before calling; a caller passing an operator-chosen URL (the RSS feed
    fetch) is already trust-gated by its own config.

    Failure paths log at WARNING so an operator watching the poll
    cycle can see WHY a challenge-protected source skipped instead
    of just "ConnectorParseError" with no upstream context. Sidecar
    timeouts are the common culprit under heavy poll load: each
    challenge solve takes seconds, FlareSolverr serializes them, so
    sources late in a long cycle can wait past their own timeout
    window."""
    endpoint = settings.SOURCE_CHALLENGE_BYPASS_URL.rstrip("/")
    if not endpoint:
        return None
    try:
        response = httpx.post(
            endpoint,
            json={
                "cmd": "request.get",
                "url": url,
                "maxTimeout": int(timeout_s * 1000),
            },
            timeout=timeout_s + 10.0,  # allow internal headroom past our cmd timeout
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "challenge-bypass sidecar unreachable at %s for %s: %s: %s",
            endpoint,
            url,
            type(exc).__name__,
            exc,
        )
        return None
    if response.status_code != 200:
        logger.warning(
            "challenge-bypass sidecar HTTP %s for %s: %s",
            response.status_code,
            url,
            response.text[:200],
        )
        return None
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("challenge-bypass sidecar returned non-JSON for %s: %s", url, exc)
        return None
    if not isinstance(payload, dict):
        # Valid JSON but not an object (null / list / bare string): a misconfigured
        # SOURCE_CHALLENGE_BYPASS_URL pointing at the wrong service. `.get` below would
        # AttributeError and (for the best-effort enrichment caller) crash the run, so
        # treat it as a miss like every other failure mode here.
        logger.warning("challenge-bypass sidecar returned non-object JSON for %s: %r", url, payload)
        return None
    if payload.get("status") != "ok":
        logger.warning(
            "challenge-bypass sidecar refused %s: %s",
            url,
            payload.get("message", "<no message>"),
        )
        return None
    body = ((payload.get("solution") or {}).get("response")) or ""
    if not body:
        return None
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > max_bytes:
        logger.warning(
            "challenge-bypass sidecar body for %s exceeded %d-byte cap (got %d) ; dropping",
            url,
            max_bytes,
            len(body_bytes),
        )
        return None
    return body_bytes
