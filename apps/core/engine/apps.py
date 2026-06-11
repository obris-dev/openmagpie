from django.apps import AppConfig


class EngineConfig(AppConfig):
    name = "engine"

    def ready(self) -> None:
        # Register the system checks (e.g. the engine.W001 ENGINE_MODEL warning).
        from engine import checks  # noqa: F401
