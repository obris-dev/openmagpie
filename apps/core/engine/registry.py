"""Engine registry: the single relevance engine, configured from Django settings.

There's one engine - `OpenAICompatEngine` against `ENGINE_BASE_URL` - because every
backend we support speaks the OpenAI `/v1` API, so no per-backend "kind" routing
is needed (the endpoint + the spec are enough). The Protocol + this thin registry
remain as the seam: the watch-action config's free-string `engine.kind` is
validated against `kinds()` (only the default), and a future divergent backend
could be registered here without touching the call sites or the config schema.
"""

from django.conf import settings

from .engines import Engine, OpenAICompatEngine, AnthropicEngine

# The default kind; an empty `engine.kind` in config resolves to it.
DEFAULT_KIND = OpenAICompatEngine.kind


def _build() -> Engine:
    kind = getattr(settings, "ENGINE_KIND", DEFAULT_KIND)
    if kind == AnthropicEngine.kind:
        return AnthropicEngine(
            base_url=settings.ENGINE_BASE_URL,
            default_model=settings.ENGINE_MODEL,
            api_key=settings.ENGINE_API_KEY,
            max_retries=settings.ENGINE_MAX_RETRIES,
        )
    return OpenAICompatEngine(
        base_url=settings.ENGINE_BASE_URL,
        default_model=settings.ENGINE_MODEL,
        api_key=settings.ENGINE_API_KEY,
        max_retries=settings.ENGINE_MAX_RETRIES,
    )


# Built eagerly at import (the constructor stores config and a socket-less client,
# no network), so there's no lazy check-then-set to race under a threaded server.
# register() swaps it for tests.
_engine: Engine = _build()


def get(kind: str = "") -> Engine:
    """The configured engine. Empty kind (or the default kind) resolves to it;
    any other kind raises KeyError so the config policy maps a bad pin to a clean
    400 at the write boundary rather than a mid-poll failure."""
    if kind and kind != DEFAULT_KIND:
        raise KeyError(kind)
    return _engine


def kinds() -> list[str]:
    """Recognized engine kinds. Used by config validation to
    reject a bad `engine.kind` at create time rather than mid-poll."""
    return [OpenAICompatEngine.kind, AnthropicEngine.kind]


def register(engine: Engine) -> None:
    """Override the engine (test-only: used to stub the LLM)."""
    global _engine
    _engine = engine
