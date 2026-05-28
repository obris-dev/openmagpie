from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from events.observations import Observation
from listeners.models import Listener
from openmagpie_schema.engine import EngineStatus


class JudgmentJSON(BaseModel):
    """LLM-output contract for any score-shaped relevance engine.

    Doubles as the structured-output schema we hand to the model (via e.g.
    Ollama's `format` field, OpenAI's `response_format`, etc.) and as the
    parser on the way back. Engine implementations are free to use it as-is
    or define their own, but the shape they map into `JudgmentResult` is
    this one.
    """

    score: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass(frozen=True)
class JudgmentResult:
    """In-memory verdict from an Engine. The engine scores relevance (0.0-1.0);
    the *hit* decision is made by the caller against a Listener-configured
    threshold (`SemanticListenerConfig.hit_threshold`)."""

    score: float
    reason: str
    model: str
    latency_ms: int
    raw_response: str


@dataclass(frozen=True)
class JudgeRequest:
    """One item to score in a batch. Shape matches the args of
    `Engine.judge`; the batch surface takes a list of these so the
    engine can fan them out concurrently behind the sync seam."""

    observation: Observation
    listener: Listener
    model: str | None = None


class EngineModelInvalid(ValueError):
    """The model a listener pinned can't be used by this engine
    instance — not loaded on the upstream server, doesn't match the
    provider's name pattern, etc. Raised by `Engine.validate_model`;
    listener policy translates to a PolicyError at the save boundary.

    Lives in the engine layer (not in listeners.policy) because the
    engine knows the failure modes; the listener layer just runs the
    check and maps the result to HTTP-shaped errors."""


class Engine(Protocol):
    """A pluggable relevance engine. Implementations live in this package."""

    kind: str
    concurrency: int
    """Max number of items this engine instance handles in parallel for
    `judge_batch`. The orchestrator uses it to size its in-flight
    window. Default 1 (sequential) is the conservative behavior;
    operators raise it via env to speed up backfills (and must match
    it server-side, e.g. `OLLAMA_NUM_PARALLEL` on the Ollama box)."""

    def judge(
        self,
        observation: Observation,
        listener: Listener,
        *,
        model: str | None = None,
    ) -> JudgmentResult:
        """Score how relevant the observation is to the listener's interest.

        Single-item facade; for cycles with many items the orchestrator
        uses `judge_batch` instead. `model` lets the caller override the
        engine's default model on a per-listener basis (so
        `SemanticListenerConfig.engine.model` isn't a no-op). None means
        "use the engine instance's configured default."
        """
        ...

    def judge_batch(self, requests: list[JudgeRequest]) -> list[JudgmentResult | Exception]:
        """Score N items concurrently; return one entry per input in
        submission order.

        Implementations fan out internally (asyncio + an async HTTP
        client for Ollama; future cloud engines might use a thread pool
        or provider SDK). Per-item failures are returned as exception
        instances (think `asyncio.gather(return_exceptions=True)`)
        rather than raising, so the orchestrator can advance the cursor
        past successes that precede a failure in the batch.

        The sync seam (this method returns a list, not a coroutine)
        keeps Django callers sync. The engine owns the bridge.
        """
        ...

    def validate_model(self, model: str) -> None:
        """Confirm `model` is usable by this engine instance. Called by
        listener config policy at save time when the listener pins a
        non-empty `engine.model`, so the operator finds out at create/
        update if their choice can't be served (vs every judge cycle
        500ing).

        Raises `EngineModelInvalid` if the model can't be used. Engines
        with no meaningful per-model check (e.g. providers whose model
        choice can't be pre-verified without sending real traffic)
        implement this as a no-op `pass` — explicit, not hasattr-checked.
        """
        ...

    def status(self) -> EngineStatus:
        """Reachability snapshot for `/v1/engines` and pre-flight UIs.

        Implementations probe the upstream (or whatever counts as
        "reachable" for the provider) and return a populated
        `EngineStatus`. Never raises: unreachable / shape-drift errors
        must be reported as `available=False` with an operator-facing
        `unreachable_reason` so a CLI consumer can render it directly.
        """
        ...
