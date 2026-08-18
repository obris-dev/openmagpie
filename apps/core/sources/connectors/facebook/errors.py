"""Error taxonomy for the Facebook (camofox) connector.

Maps subprocess / facebook-camofox-client failures to canonical error
shapes with retry semantics, following the same pattern as the Twitter
connector's ListenerError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FacebookError(Exception):
    """Canonical error shape for one Facebook fetch failure."""

    code: str  # stable machine code
    message: str  # human-readable
    retryable: bool  # safe to retry with backoff?
    action: str  # what the ops layer should do
    context: dict[str, Any] = field(default_factory=dict)


def map_worker_error(worker_output: dict, context: dict[str, Any] | None = None) -> FacebookError:
    """Translate a facebook-worker.py error response into a FacebookError."""
    code = worker_output.get("code", "worker_error")
    message = worker_output.get("error", "Unknown worker error")
    # Auth failures
    if code in ("auth_required",):
        return FacebookError(
            code="auth_required",
            message=message,
            retryable=False,
            action="refresh Facebook cookies in Twenty _socialAccount record",
            context=context or {},
        )

    # Session expired
    if code == "session_expired" or "session expired" in message.lower():
        return FacebookError(
            code="session_expired",
            message=message,
            retryable=False,
            action="refresh Facebook cookies; session has expired",
            context=context or {},
        )

    # Browser init failure
    if "browser" in message.lower() or "camoufox" in message.lower() or "init" in message.lower():
        return FacebookError(
            code="browser_init_failed",
            message=message,
            retryable=True,
            action="retry with backoff; browser binary may be downloading",
            context=context or {},
        )

    # Generic worker error
    return FacebookError(
        code=code,
        message=message,
        retryable=True,
        action="retry with backoff; log and alert after 5 consecutive",
        context=context or {},
    )
