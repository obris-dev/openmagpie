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

import httpx

from openmagpie_schema.watch_actions import ExternalContentStatus
from sources.connectors.base import ConnectorParseError, extract_article_text

logger = logging.getLogger("watches")

# Bounds for the opt-in external-article fetch (config.fetch_external_content).
MAX_ARTICLE_BYTES = 5 * 1024 * 1024
ARTICLE_USER_AGENT = "openmagpie/1.0 (+https://github.com/obris-dev/openmagpie)"


def resolve_external_content(
    fetch_url: Callable[..., bytes],
    *,
    action_id: str,
    enabled: bool,
    external_url: str,
) -> tuple[str | None, ExternalContentStatus]:
    """Lazily fetch + extract an item's external link for an LLM call. Returns
    (content, status): the status is recorded on the run result so the call
    carries whether it saw the article, and if not, why. `fetch_url` is the
    caller's `self.fetch_external_url` (the SSRF-safe fetch).

    Best-effort by design -- a failed fetch (UNAVAILABLE) or an empty extraction
    (MISSING) still returns None so the LLM runs on title + content ; the run is
    never failed for missing enrichment. Only the EXPECTED fetch failures are
    swallowed (httpx / ConnectorParseError: blocked host, oversize, timeout,
    4xx/5xx) ; an unexpected error propagates, since that is a real defect."""
    if not enabled:
        return None, ExternalContentStatus.DISABLED
    if not external_url:
        return None, ExternalContentStatus.NOT_APPLICABLE
    try:
        html = fetch_url(external_url, max_bytes=MAX_ARTICLE_BYTES, user_agent=ARTICLE_USER_AGENT)
    except (httpx.HTTPError, ConnectorParseError) as exc:
        # warning (not info): a fetch FAILURE can signal a systemic egress /
        # network problem, not just one dead link. (The per-run UNAVAILABLE
        # status is the primary signal; MISSING / paywalls stay unlogged.)
        logger.warning("external fetch failed for action=%s url=%s: %s", action_id, external_url, exc)
        return None, ExternalContentStatus.UNAVAILABLE
    text = extract_article_text(html)
    if not text:
        # Fetched OK but no usable article text (paywall / JS-only / non-article).
        return None, ExternalContentStatus.MISSING
    return text, ExternalContentStatus.INCLUDED
