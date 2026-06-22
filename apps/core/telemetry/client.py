"""PostHog client wrapper: the single choke point for emitting telemetry.

Everything goes through `capture`, which enforces the privacy contract: it emits
ONLY when the instance is in ANONYMOUS mode (the sole self-hosted "on" state),
`DO_NOT_TRACK` is unset, and an ingestion key is configured. It attaches the
anonymous `instance_id` as the distinct_id plus the product version, and it
NEVER raises: a telemetry failure must never disturb the request / poll / drain
path it is called from. Telemetry is uniformly best-effort -- every emit path
(here, `events.guard`, the heartbeat command) swallows + logs via a deliberate,
adjacent-commented broad-except, so a hiccup degrades silently instead of
propagating.
"""

import functools
import logging
import os
import threading
from typing import Any

from django.conf import settings

from .constants import DEPLOYMENT_SELF_HOSTED
from .models import TelemetrySettings

_UNKNOWN_VERSION = "unknown"

logger = logging.getLogger("telemetry")

_client_built = False
_client_instance: Any = None
_client_lock = threading.Lock()


def get_client() -> Any:
    """Lazily build (once) and return the PostHog client, or None if telemetry
    has no ingestion key configured / the SDK fails to initialise.

    Double-checked locking: the fast path is a lockless read of the already-built
    flag; only the first call(s) take the lock to build. Without it, two
    concurrent first-requests under a threaded worker could both construct a
    client and leak the loser's background flush thread. The flag is set True even
    on init failure, so a broken build is not retried on every call."""
    global _client_built, _client_instance
    if _client_built:
        return _client_instance
    with _client_lock:
        if _client_built:
            return _client_instance
        key = settings.POSTHOG_API_KEY
        instance: Any = None
        if key:
            try:
                from posthog import Posthog

                instance = Posthog(
                    project_api_key=key,
                    host=settings.POSTHOG_HOST,  # base settings always define this (env or default)
                    # Never geolocate events: an IP-derived location would
                    # undercut the "anonymous" promise.
                    disable_geoip=True,
                )
            except Exception:
                logger.exception("PostHog client init failed")
                instance = None
        # Assign the instance BEFORE flipping the flag: a lockless reader that
        # sees built == True must also see the fully-assigned instance.
        _client_instance = instance
        _client_built = True
    return _client_instance


def do_not_track() -> bool:
    # consoledonottrack.com convention: honor any set, non-"0"/non-empty value.
    # Public (no underscore): the `telemetry status` command reads it too, so it's
    # the single source for "is emission suppressed", not a re-derived predicate.
    return os.environ.get("DO_NOT_TRACK", "").strip().lower() not in ("", "0", "false")


@functools.cache  # version.txt is immutable for a process's life -- read once, not per emit
def _product_version() -> str:
    try:
        return (settings.REPO_ROOT / "version.txt").read_text(encoding="utf-8").strip() or _UNKNOWN_VERSION
    except (OSError, UnicodeDecodeError):
        # A missing OR non-UTF8 version.txt degrades to "unknown" + still emits;
        # without catching the decode error the event would be dropped by capture's
        # outer try, taking all telemetry dark on a malformed file.
        return _UNKNOWN_VERSION


def enabled() -> bool:
    """Whether telemetry will actually emit: opted into ANONYMOUS, DO_NOT_TRACK
    unset, and an ingestion key configured. A hot call site can check this to skip
    expensive prop-gathering before it builds an event (capture() re-checks too).
    UNSET / OFF emit nothing (opt-in); IDENTIFIED is hosted-only + refused on
    self-hosted, so ANONYMOUS is the only emitting mode here."""
    # Free checks first (env var + settings string). A stock install ships the
    # baked-in key, so POSTHOG_API_KEY is non-empty and this falls through to the
    # singleton read on every call (only a keyless / DO_NOT_TRACK install skips the
    # DB). That read is a 1-row indexed lookup -- negligible next to the LLM-bound
    # drain or a user-driven create -- so it's deliberately left uncached.
    if do_not_track() or not settings.POSTHOG_API_KEY:
        return False
    return TelemetrySettings.current().is_anonymous


def capture(event: str, properties: dict[str, Any] | None = None) -> None:
    """Emit one telemetry event, or silently no-op. Never raises."""
    try:
        # Inline the enabled() gate so the singleton row is read ONCE: free checks
        # first, then a single current() that serves both the mode gate and the
        # distinct_id (calling enabled() here would read the row a second time).
        if do_not_track() or not settings.POSTHOG_API_KEY:
            return
        row = TelemetrySettings.current()
        # `not instance_id` defends an anonymous row whose id was never minted (a
        # restored backup / hand-edited row): a distinct_id="" would lump every such
        # install under one empty id. set_mode always mints it on the normal path.
        if not row.is_anonymous or not row.instance_id:
            return
        client = get_client()
        if client is None:
            return
        # Caller props first, trusted server-stamped keys last: version/deployment
        # are authoritative and must win any (latent) key collision from a caller.
        props = {**(properties or {}), "version": _product_version(), "deployment": DEPLOYMENT_SELF_HOSTED}
        client.capture(event, distinct_id=row.instance_id, properties=props)
    except Exception:
        logger.exception("capture failed for event=%s", event)


def flush() -> None:
    """Flush batched events. Call before a short-lived management command exits
    so its events aren't lost when the background flush thread is torn down."""
    try:
        client = get_client()
        if client is not None:
            client.flush()
    except Exception:
        logger.exception("flush failed")
