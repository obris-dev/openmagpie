"""Error taxonomy for the YouTube (yt-dlp) connector.

Maps yt-dlp exceptions to canonical error shapes with retry semantics,
following the same pattern as the Twitter connector's TwitterError.
"""

from __future__ import annotations

from typing import Any


class YouTubeError(Exception):
    """Canonical error shape for one YouTube fetch failure."""

    def __init__(
        self,
        *,
        code: str,  # stable machine code
        message: str,  # human-readable
        retryable: bool,  # safe to retry with backoff?
        action: str,  # what the ops layer should do
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.action = action
        self.context = context or {}


def map_ytdlp_error(exc: Exception, context: dict[str, Any] | None = None) -> YouTubeError:
    """Translate an yt-dlp exception into a canonical YouTubeError."""
    msg = str(exc)

    # Video not available (region-restricted, deleted, private)
    if "This video is not available" in msg or "Video unavailable" in msg:
        return YouTubeError(
            code="video_unavailable",
            message=msg,
            retryable=False,
            action="skip (video no longer available)",
            context=context or {},
        )

    # Rate limiting / throttling
    if "rate limited" in msg.lower() or "too many requests" in msg.lower():
        return YouTubeError(
            code="rate_limited",
            message=msg,
            retryable=True,
            action="retry with exponential backoff",
            context=context or {},
        )

    # Missing JavaScript runtime (warning only, still works in degraded mode)
    if "No supported JavaScript runtime" in msg:
        return YouTubeError(
            code="js_runtime_missing",
            message=msg,
            retryable=False,
            action="install deno or node; proceeding in degraded mode",
            context=context or {},
        )

    # Network/connection errors
    if any(marker in msg.lower() for marker in ["connection", "timeout", "network", "urlopen"]):
        return YouTubeError(
            code="network_error",
            message=msg,
            retryable=True,
            action="retry with backoff",
            context=context or {},
        )

    # Generic fallback
    return YouTubeError(
        code="yt_dlp_error",
        message=msg,
        retryable=True,
        action="log and retry with backoff",
        context=context or {},
    )
