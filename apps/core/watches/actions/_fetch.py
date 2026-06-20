"""ExternalFetchMixin: the one way an Action fetches an arbitrary, submitter- or
user-supplied URL.

Any action that needs to pull a URL whose value comes from item / user data
inherits this and calls `self.fetch_external_url(...)`. It routes through the
SSRF-safe pinned-IP transport (`common.safe_http`, via `sources.connectors.base`),
so a new fetching action gets the private-IP block + DNS-rebinding protection for
free instead of reaching for `httpx` directly and silently bypassing it.

This is a CONVENTION carrier, not heavy machinery: keep the safe path the obvious
path. Do NOT fetch an untrusted URL with raw `httpx` in an action; add the method
here if a real fetch needs an option this doesn't expose.
"""

from __future__ import annotations

from sources.connectors.base import fetch_url_safely


class ExternalFetchMixin:
    """Gives an Action `fetch_external_url`: the SSRF-safe way to GET an untrusted
    URL (pinned-IP transport + streamed byte cap)."""

    def fetch_external_url(
        self,
        url: str,
        *,
        max_bytes: int,
        user_agent: str | None = None,
        timeout: float = 15.0,
    ) -> bytes:
        """GET an untrusted `url` through the pinned-IP SSRF guard; return the raw
        body. Exception contract is `fetch_url_safely`'s (best-effort callers catch
        broadly). Extraction (e.g. HTML -> text) is the caller's concern; this only
        fetches."""
        return fetch_url_safely(url, max_bytes=max_bytes, user_agent=user_agent, timeout=timeout)
