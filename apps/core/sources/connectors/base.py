import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Protocol

import httpx
from django.conf import settings
from pydantic import BaseModel

from common.safe_http import SsrfBlocked, pinned_transport
from common.ssrf import destination_block_reason
from sources.payloads import SourcePayload

logger = logging.getLogger("sources")

# Bounds for fetching an UNTRUSTED URL (the engine's article fetch): cap the
# redirect chain and the total wall-clock, so a redirect bomb or a slowloris
# endpoint can't tie up a drain worker. (httpx's `timeout` is per-phase, not a
# total deadline, so neither alone bounds the whole fetch.)
FETCH_MAX_REDIRECTS = 3
FETCH_TOTAL_TIMEOUT = 30.0


class ConnectorParseError(Exception):
    """A connector failed to parse a response from its upstream source.

    Raised when a 200 OK arrives with a body that isn't the shape we expect
    (HTML instead of JSON, payload schema change, missing required keys).
    Connectors should translate library-specific failures (json.JSONDecodeError,
    KeyError, TypeError on a dict walk, etc.) into this so the polling
    orchestrator can recover at the per-source boundary without having to
    enumerate every parser library's exception types.
    """


def read_response_capped(
    response: httpx.Response, *, max_bytes: int, url_label: str, deadline: float | None = None
) -> bytes:
    """Drain a streaming response into bytes, raising once the running
    total crosses `max_bytes`. The cap fires mid-stream so a hostile
    endpoint serving a multi-GB body never buffers past the cap (a
    `response.content`-then-`len` check materializes the full body
    before deciding).

    `deadline` (a `time.monotonic()` value), when given, also caps total
    wall-clock across the read loop, so a slowloris dribbling bytes within the
    per-chunk read timeout can't read forever. Caller is responsible for
    `response.raise_for_status()` ; the helper only reads bytes. Shared between
    connectors so the cap policy lives in one place."""
    body = bytearray()
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() > deadline:
            raise ConnectorParseError(f"{url_label} exceeded its fetch time budget mid-stream")
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ConnectorParseError(f"{url_label} exceeded {max_bytes}-byte cap mid-stream")
    return bytes(body)


# ── SSRF-safe fetch of the open web ───────────────────────────────────────
# Two callers, one block POLICY (`common.ssrf` / `common.safe_http`):
#   - RSS feeds (OPERATOR-chosen URLs): `validate_request_url`, an httpx request
#     hook gated by SOURCE_BLOCK_PRIVATE_IPS (an operator may point at an internal
#     feed). Re-checked on every redirect.
#   - the engine's lazy article fetch (UNTRUSTED submitter URLs): `fetch_url_safely`,
#     on the pinned-IP transport (always-block; resolve once + connect to that IP,
#     closing the DNS-rebinding TOCTOU).


def validate_request_url(request: httpx.Request) -> None:
    """httpx request hook for OPERATOR-chosen URLs (RSS feeds): block a private /
    loopback / etc. target only when SOURCE_BLOCK_PRIVATE_IPS is on (an operator
    may deliberately point at an internal feed). Runs on the request and every
    redirect."""
    reason = destination_block_reason(
        str(request.url),
        require_https=False,
        block_private_ips=settings.SOURCE_BLOCK_PRIVATE_IPS,
        resolve_dns=True,
    )
    if reason:
        raise ConnectorParseError(f"blocked URL {request.url}: {reason}")


def fetch_url_safely(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 15.0,
    user_agent: str | None = None,
    _transport: httpx.BaseTransport | None = None,
) -> bytes:
    """GET an UNTRUSTED `url` with pinned-IP SSRF protection (resolve once,
    validate, connect to that IP; re-pinned per redirect) plus the streamed byte
    cap; return the raw body. The redirect chain is capped at FETCH_MAX_REDIRECTS
    and total wall-clock at FETCH_TOTAL_TIMEOUT, so a hostile link can't stall a
    worker. Raises ConnectorParseError on a blocked host / oversize body / time
    budget, httpx.* on transport / non-2xx errors. Best-effort callers (the
    engine's article fetch) catch broadly and fall back. `_transport` is a
    TEST-ONLY override; a real caller must never pass it or SSRF protection is
    bypassed."""
    headers = {"User-Agent": user_agent} if user_agent else {}
    client_transport = pinned_transport() if _transport is None else _transport
    deadline = time.monotonic() + FETCH_TOTAL_TIMEOUT
    try:
        with (
            httpx.Client(
                transport=client_transport,
                follow_redirects=True,
                max_redirects=FETCH_MAX_REDIRECTS,
                timeout=timeout,
                headers=headers,
            ) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            return read_response_capped(response, max_bytes=max_bytes, url_label=url, deadline=deadline)
    except SsrfBlocked as exc:
        raise ConnectorParseError(str(exc)) from exc


def extract_article_text(html: bytes, *, max_chars: int = 20_000) -> str:
    """Extract the main readable text from an HTML page (boilerplate stripped),
    capped to `max_chars`. Returns "" when nothing useful is extractable (a
    paywall, a JS-only page, a non-article) OR when extraction itself fails -- ""
    is this helper's own failure mode, so a best-effort caller never crashes.
    trafilatura is imported lazily so the dep only loads when an extraction runs."""
    import trafilatura

    try:
        # Pass raw bytes: trafilatura detects the page's charset itself ; decoding
        # as utf-8 first would mangle a non-utf-8 page (latin-1) before extraction.
        text = trafilatura.extract(html) or ""
    except Exception:
        # Blanket catch is deliberate HERE: a leaf over UNTRUSTED bytes whose
        # trafilatura / lxml failure surface is unbounded (parse errors, recursion
        # limits) and whose defined failure value is "". logger.exception (not
        # warning) so the full traceback reaches Sentry -- a crash is unexpected,
        # unlike the caller's narrowly-handled fetch errors.
        logger.exception("extract_article_text: extraction failed")
        return ""
    return text[:max_chars].strip()


class Connector[SpecT: BaseModel](Protocol):
    """A pluggable source connector.

    Generic over `SpecT`, the connector's concrete `SourceSpec` variant
    (e.g. `RssSourceSpec`). Binding the variant lets `poll` keep typed
    access to its own spec fields (`spec.url`, `spec.subreddit`) while
    still satisfying this protocol ; the kind-keyed registry holds
    `Connector[Any]` because dispatch erases the variant at the call seam.

    Each implementation:
      - declares its `kind` (matches `SourceSpec.kind`),
      - declares its `payloads` (SourcePayload subclasses it produces, used
        by `sources.payload_registry` to hydrate stored data back to typed
        SourcePayloads),
      - yields typed SourcePayloads for a given stream_spec.

    Connectors are tenant-agnostic: the Feed drives polling, so `poll` takes
    only the source spec + watermark (no watch/account).
    """

    kind: str
    payloads: list[type[SourcePayload]]

    def poll(
        self,
        spec: SpecT,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:
        """Yield typed SourcePayloads for one source, newer than `since`.

        `field_map` is the EFFECTIVE map for this source ; the polling
        orchestrator merges the feed's `default_field_map` with the
        Source row's `field_map` (row wins per key) and passes the
        result. Connectors that don't read it (e.g. Reddit) accept and
        ignore. Recognized keys are per-connector; unknown keys are
        silently dropped. None == empty dict.

        `heartbeat` is a liveness tick for long INTENTIONAL waits (e.g.
        sleeping out a rate limit): call it every several seconds mid-wait
        so the orchestrator can renew its poll lease ; the lease detects
        dead holders, and a deliberate wait is alive. Connectors IGNORE
        its return value: lease loss is handled at the orchestrator's
        between-source seam, renewing a lost lease is a no-op, and the
        worst case of finishing the source anyway is redundant idempotent
        work. Connectors with no long waits accept and ignore. None == no
        liveness to report (direct calls / tests)."""
        ...

    def count(
        self,
        spec: SpecT,
        since: datetime | None,
    ) -> int:
        """Exact count of payloads newer than `since`. Used by the
        polling op's warm path to give progress UIs an `N/total` and
        an ETA.

        This is a structural signature only. There is no free default
        from this Protocol; inherit `BaseConnector` to get the universal
        poll-walk implementation, or implement `count` yourself.
        """
        ...


class BaseConnector[SpecT: BaseModel]:
    """Concrete base supplying the universal `count` implementation.

    Generic over `SpecT` like `Connector` ; a subclass binds the variant
    (`BaseConnector[RssSourceSpec]`) so its `poll` override keeps the same
    spec type and doesn't trip a Liskov check.

    Inherit this and a new connector gets a correct `count` for free:
    it re-walks `poll` and discards each payload. That doubles the
    upstream bandwidth for a warm cycle, but the payload construction
    is microseconds, negligible next to per-payload LLM judging.
    Override `count` only if your upstream has a cheaper exact-count path.

    `BaseConnector` itself is NOT a `Connector` (it has no `kind` /
    `payloads`); a concrete subclass that declares those plus `poll`
    is what structurally satisfies the Protocol. This class only supplies
    the `count` default, it is opt-in, not a required parent.
    """

    def count(
        self,
        spec: SpecT,
        since: datetime | None,
    ) -> int:
        return sum(1 for _ in self.poll(spec, since=since))

    def poll(
        self,
        spec: SpecT,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> Iterator[SourcePayload]:  # pragma: no cover - subclass responsibility
        raise NotImplementedError
