from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Protocol

import httpx
from pydantic import BaseModel

from sources.payloads import SourcePayload


class ConnectorParseError(Exception):
    """A connector failed to parse a response from its upstream source.

    Raised when a 200 OK arrives with a body that isn't the shape we expect
    (HTML instead of JSON, payload schema change, missing required keys).
    Connectors should translate library-specific failures (json.JSONDecodeError,
    KeyError, TypeError on a dict walk, etc.) into this so the polling
    orchestrator can recover at the per-source boundary without having to
    enumerate every parser library's exception types.
    """


def read_response_capped(response: httpx.Response, *, max_bytes: int, url_label: str) -> bytes:
    """Drain a streaming response into bytes, raising once the running
    total crosses `max_bytes`. The cap fires mid-stream so a hostile
    endpoint serving a multi-GB body never buffers past the cap (a
    `response.content`-then-`len` check materializes the full body
    before deciding).

    Caller is responsible for `response.raise_for_status()` ; the helper
    only reads bytes. Shared between connectors so the cap policy lives
    in one place."""
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ConnectorParseError(f"{url_label} exceeded {max_bytes}-byte cap mid-stream")
    return bytes(body)


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
