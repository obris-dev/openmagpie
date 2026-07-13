"""Shared linked-article enrichment for actions that fold an item's external
link into an LLM call (semantic_filter, extract).

One copy of the fetch -> extract -> status orchestration so the two actions
can't drift. The actual GET goes through the caller's `ExternalFetchMixin`
(`fetch_external_url`, the SSRF-safe pinned-IP transport); this module owns the
opt-in/no-op rules, the best-effort failure swallowing, and the resulting
`ExternalContentStatus` provenance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from common.ssrf import destination_block_reason
from openmagpie_schema.watch_actions import ExternalContentStatus
from sources.connectors.base import FETCH_DEFAULT_MAX_BYTES, ConnectorParseError, extract_article_text
from sources.connectors.challenge_bypass import challenge_bypass_fetch

logger = logging.getLogger("watches")

# Bounds for the opt-in external-article fetch (config.fetch_external_content); the
# shared untrusted-fetch cap so the article + sidecar + RSS caps can't drift apart.
MAX_ARTICLE_BYTES = FETCH_DEFAULT_MAX_BYTES
ARTICLE_USER_AGENT = "openmagpie/1.0 (+https://github.com/obris-dev/openmagpie)"

# Primary-fetch outcomes a Cloudflare-style block produces, i.e. the ones worth
# retrying through the headless challenge-bypass sidecar: the fetch was refused
# (UNAVAILABLE) or it returned an interstitial with no extractable article (MISSING).
_FALLBACK_STATUSES = (ExternalContentStatus.UNAVAILABLE, ExternalContentStatus.MISSING)


def resolve_external_content(
    fetch_url: Callable[..., bytes],
    *,
    action_id: str,
    enabled: bool,
    article_url: str,
) -> tuple[str | None, ExternalContentStatus]:
    """Lazily fetch + extract an item's linked article for an LLM call. Returns
    (content, status): the status is recorded on the run result so the call
    carries whether it saw the article, and if not, why. `fetch_url` is the
    caller's `self.fetch_external_url` (the SSRF-safe fetch); `article_url` is the
    per-kind fetch target the caller picked (see sources.payloads.SourcePayload.article_url).

    Best-effort by design: a failed fetch (UNAVAILABLE) or an empty extraction
    (MISSING) still returns None so the LLM runs on title + content ; the run is
    never failed for missing enrichment. Only the EXPECTED fetch failures are
    swallowed (httpx / ConnectorParseError: blocked host, oversize, timeout,
    4xx/5xx) ; an unexpected error propagates, since that is a real defect.

    When the direct (pinned-IP) fetch hits a Cloudflare-style wall and a
    challenge-bypass sidecar is configured, retry through it (INCLUDED_VIA_FALLBACK).
    See `_challenge_fallback` for the SSRF trade-off that path accepts."""
    if not enabled:
        return None, ExternalContentStatus.DISABLED
    if not article_url:
        return None, ExternalContentStatus.NOT_APPLICABLE
    try:
        html = fetch_url(article_url, max_bytes=MAX_ARTICLE_BYTES, user_agent=ARTICLE_USER_AGENT)
    except (httpx.HTTPError, httpx.InvalidURL, ConnectorParseError) as exc:
        # httpx.InvalidURL is a SIBLING of HTTPError, not a subclass, so it must be named
        # explicitly: a malformed item-derived URL (non-numeric port, unterminated IPv6)
        # raises it while building the request, and enrichment is best-effort, so a bad
        # URL must degrade to UNAVAILABLE, never crash the run.
        #
        # warning (not info): a fetch FAILURE can signal a systemic egress /
        # network problem, not just one dead link. (The per-run UNAVAILABLE
        # status is the primary signal; MISSING / paywalls stay unlogged.)
        logger.warning("external fetch failed for action=%s url=%s: %s", action_id, article_url, exc)
        status: ExternalContentStatus = ExternalContentStatus.UNAVAILABLE
    else:
        text = extract_article_text(html)
        if text:
            return text, ExternalContentStatus.INCLUDED
        # Fetched OK but no usable article text (paywall / JS-only / non-article).
        status = ExternalContentStatus.MISSING

    if status in _FALLBACK_STATUSES:
        recovered = _challenge_fallback(article_url, action_id=action_id)
        if recovered is not None:
            return recovered, ExternalContentStatus.INCLUDED_VIA_FALLBACK
    return None, status


def _is_fetchable_web_url(url: str, *, action_id: str) -> bool:
    """Whether `url` is a well-formed http(s) URL with a host, i.e. shape-safe to hand
    to the challenge-bypass sidecar's browser. Rejects (and logs) two cases:
      - a URL stdlib `urlsplit` can't parse (malformed, e.g. an unterminated IPv6
        `http://[::1`). This is a DIFFERENT parser from httpx's on the direct path, so
        the crash it raises must be guarded here or it fails the run.
      - a hostless or non-http(s) scheme (file:// data: gopher:), which must never reach
        the browser.
    This is only the URL SHAPE. The private-IP / DNS-resolution SSRF check is separate
    (`destination_block_reason` in the caller)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        logger.warning("challenge-bypass rejected malformed url action=%s url=%s", action_id, url)
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logger.warning("challenge-bypass rejected non-web url action=%s url=%s", action_id, url)
        return False
    return True


def _challenge_fallback(article_url: str, *, action_id: str) -> str | None:
    """Retry a walled article through the headless challenge-bypass sidecar; the
    extracted text, or None if unconfigured / blocked / still no article.

    SSRF: the sidecar does its OWN egress (real browser), so the pinned-IP guard
    that protects the direct fetch CANNOT apply, and `article_url` here is UNTRUSTED
    (item-derived). Before the handoff we (a) require a shape-safe http(s) URL with a
    host (`_is_fetchable_web_url`, which also guards the malformed-URL crash), and (b)
    refuse anything that IS or RESOLVES TO a private / loopback / reserved address
    (block_private_ips=True unconditionally, not gated on SOURCE_BLOCK_PRIVATE_IPS), so
    the browser is never pointed at the internal network.

    Three residual gaps remain, all inherent to delegating egress to the sidecar and
    accepted because it is a self-hosted, operator-opted-in service (must be revisited
    before any hosted / multi-tenant use): a DNS-rebinding TOCTOU (the sidecar
    re-resolves at fetch time, so the host we cleared can change); redirect-follow (the
    sidecar's browser follows a 302 from a cleared public host to an internal target,
    which our pre-check never sees); and a parser differential (we validate the host as
    Python's urlsplit parses it, but the sidecar's Chrome navigates the raw string under
    WHATWG rules, e.g. backslashes, so a crafted URL could resolve to a different host
    than we cleared). The capability is OFF unless
    SOURCE_CHALLENGE_BYPASS_URL is set; check that FIRST so a disabled deployment never
    even does the DNS lookup."""
    if not settings.SOURCE_CHALLENGE_BYPASS_URL:
        return None
    if not _is_fetchable_web_url(article_url, action_id=action_id):
        return None
    reason = destination_block_reason(article_url, require_https=False, block_private_ips=True, resolve_dns=True)
    if reason:
        logger.warning("challenge-bypass SSRF pre-check blocked action=%s url=%s: %s", action_id, article_url, reason)
        return None
    html = challenge_bypass_fetch(article_url, max_bytes=MAX_ARTICLE_BYTES)
    if html is None:
        return None  # sidecar unreachable / refused / oversize (already logged there)
    return extract_article_text(html) or None
