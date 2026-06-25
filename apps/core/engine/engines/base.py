from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from openmagpie_schema.engine import EngineStatus
from openmagpie_schema.watch_actions import ExtractField
from sources.payloads import SourcePayload


class JudgmentJSON(BaseModel):
    """LLM-output contract for any score-shaped relevance engine.

    Doubles as the structured-output schema we hand to the model (via the
    OpenAI `response_format`) and as the parser on the way back. Engine
    implementations are free to use it as-is or define their own, but the
    shape they map into `JudgmentResult` is this one.
    """

    score: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass(frozen=True)
class JudgmentResult:
    """In-memory verdict from an Engine. The engine scores relevance (0.0-1.0);
    the pass / fail decision is made by the caller against a configured
    threshold (a semantic-filter action's `hit_threshold`)."""

    score: float
    reason: str
    model: str
    latency_ms: int
    raw_response: str


@dataclass(frozen=True)
class ExtractionResult:
    """In-memory result from an Engine's `extract()`. `extracted` is the
    `{declared field name: value}` map, already coerced to the declared keys
    with string values (a field the model couldn't fill is `""`). The caller
    (the extract action) maps this onto the persisted result shape and decides
    completeness; the engine stays free of the result-status enum."""

    extracted: dict[str, str]
    model: str
    latency_ms: int
    raw_response: str


class EngineRequestRejected(ValueError):
    """A `judge()` / `extract()` call was rejected by the engine in a way that
    PROVES a permanent request/config defect - retrying won't fix it. The cases: the
    backend doesn't support OpenAI structured outputs (json_schema), or the
    auth/endpoint/model 4xx's (401/403 bad `ENGINE_API_KEY`; 404 wrong
    `ENGINE_BASE_URL`/`ENGINE_MODEL`; 400/422 a malformed request).

    Distinct from transient failures (engine down, rate-limited, malformed JSON),
    which keep propagating as recoverable. The action runner maps this to an
    ERRORED run - like the unknown-engine-kind path - so the operator sees the
    exact fix instead of the same 4xx retrying every cycle."""


class Engine(Protocol):
    """A pluggable relevance engine. Implementations live in this package."""

    kind: str

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
        external_content: str | None = None,
    ) -> JudgmentResult:
        """Score how relevant the payload is to the caller's `instructions`.

        `model` lets the caller override the engine's default model on a
        per-call basis (so a pinned `engine.model` isn't a no-op). None
        means "use the engine instance's configured default."

        `external_content`, when given, is the readable text of the item's
        linked article (the caller fetched it); the engine folds it into the
        judged input alongside title + content. None = judge on the payload
        alone.
        """
        ...

    def extract(
        self,
        payload: SourcePayload,
        *,
        fields: list[ExtractField],
        instructions: str = "",
        model: str | None = None,
        external_content: str | None = None,
    ) -> ExtractionResult:
        """Pull the declared `fields` out of the payload (hydration, not scoring).

        Returns an `ExtractionResult` whose `extracted` map has exactly the
        declared field names as keys and string values (a field the model
        couldn't determine is `""`). `instructions` is optional free-form
        steering for the whole extraction. `model` / `external_content` mean the
        same as in `judge`. Raises `EngineRequestRejected` on a permanent
        request/config defect; a malformed reply propagates as a transient error.
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
