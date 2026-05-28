"""Engine registry. Maps kind string → Engine instance, configured from Django settings."""

from django.conf import settings

from .engines import Engine, OllamaEngine

_REGISTRY: dict[str, Engine] = {
    OllamaEngine.kind: OllamaEngine(
        url=settings.OLLAMA_URL,
        default_model=settings.OLLAMA_DEFAULT_MODEL,
        concurrency=settings.OLLAMA_CONCURRENCY,
    ),
}


def get(kind: str) -> Engine:
    """Raises KeyError if the kind has no registered engine."""
    return _REGISTRY[kind]


def kinds() -> list[str]:
    """All registered engine kinds, sorted. Used by config validation to
    reject a bad `engine.kind` at create time rather than mid-poll."""
    return sorted(_REGISTRY)


def register(engine: Engine) -> None:
    _REGISTRY[engine.kind] = engine
