"""The semantic_filter kind: LLM relevance gate config + result."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from .base import WatchActionConfigBase, WatchActionConfigSummary


class EngineSpec(BaseModel):
    """Which engine + model a semantic filter uses to score relevance.

    `kind == ""` means "use the server default" ; the server fills it
    from settings and rejects an unregistered kind (policy ; the pure
    package can't know the registry). `model`, when non-empty, is the
    per-call model override the engine judges with for this filter (else
    the engine's server-side default).
    """

    kind: str = ""
    model: str = ""


class SemanticFilterConfig(WatchActionConfigBase):
    """Config for a WatchAction with kind == 'semantic_filter'.

    The LLM relevance gate: scores each item against `instructions` with
    `engine`, and the run GATES the chain when the score is below
    `threshold` (a pass=false). It is the v2 home for what the old
    Listener carried as instructions + engine + hit_threshold ; it owns
    no feed (the Watch subscribes to feeds) and no delivery (that's the
    delivery actions)."""

    CONFIG_KIND: ClassVar[str] = "semantic_filter"

    # What the engine scores items against (required ; an empty filter
    # would pass everything and defeat the purpose).
    instructions: str
    # default = EngineSpec(kind=""); the server fills the real default
    # kind from settings + validates it (policy).
    engine: EngineSpec = Field(default_factory=EngineSpec)
    # The run passes (advances the chain) when score >= threshold, else
    # GATES. Strict `gt=0.0` so a 0 threshold can't pass every item ; an
    # engine returning 0 for "irrelevant" would otherwise never gate.
    threshold: float = Field(default=0.8, gt=0.0, le=1.0)

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """No secrets in a semantic filter (instructions / engine /
        threshold are all non-secret), so a plain dump is safe. The
        contract is here so a future secret-bearing kind can't ship
        without implementing it."""
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        # engine.kind == "" is the documented "use server default" ; render
        # a placeholder rather than an empty token so the preview reads
        # (e.g. "engine(default) >= 0.80", not a bare ">= 0.80").
        kind = self.engine.kind or "default"
        engine = f"{kind} | {self.engine.model}" if self.engine.model else f"engine({kind})"
        return WatchActionConfigSummary(detail=f"{engine} >= {self.threshold:.2f}")

    def merge_preserving(self, prior: WatchActionConfigBase) -> SemanticFilterConfig:
        """Nothing to carry forward: a semantic filter has no masked
        secrets or runtime state, so the submitted config wins wholesale."""
        return self


class SemanticFilterResult(BaseModel):
    """Result a semantic-filter run writes to WatchActionRun.result.

    `passed` is the gate decision (False -> the run is GATED, the chain
    stops) ; `score` is the engine's relevance in [0.0, 1.0] (kept for the
    audit log + threshold tuning), bounded to match the engine contract +
    `SemanticFilterConfig.threshold` so an out-of-range score is rejected
    at the write boundary, not silently logged. `passed` not `pass`
    because `pass` is a Python keyword."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str = ""
