"""Engine API wire shapes.

Shared, zero-Django source of truth for `/v1/engines` responses. Each
registered relevance engine reports its `EngineStatus` (kind +
configured default model + reachability + the upstream's loaded models)
so a caller can pre-flight engine availability before a config save and
surface "the LLM is unreachable at <url>" instead of discovering it at
the first judge cycle.

`unreachable_reason` is None when `available` is True; when False it
carries a short operator-facing string (the exception class name +
message) naming the URL the server tried to hit.
"""

from pydantic import BaseModel


class EngineStatus(BaseModel):
    """Per-engine reachability snapshot.

    `default_model` is what the engine would use when a config leaves
    `engine.model` empty (the server-side default). It may be empty when
    the server has no default model configured.

    `available_models` is the upstream's loaded set when reachable;
    when unreachable it's an empty list (the server didn't see anything
    and shouldn't pretend it did).
    """

    kind: str
    default_model: str = ""
    available: bool
    available_models: list[str] = []
    unreachable_reason: str | None = None
    # Operator-facing recovery hint, kind-specific (and may be
    # failure-mode-specific within a kind). None when no actionable
    # advice applies — the renderer just skips it. Knowledge lives next
    # to the engine; the CLI renders verbatim so adding an engine
    # doesn't drag the wizard along.
    how_to_fix: str | None = None


class EngineListResponse(BaseModel):
    """`GET /v1/engines` envelope. One entry per registered engine kind."""

    items: list[EngineStatus] = []
