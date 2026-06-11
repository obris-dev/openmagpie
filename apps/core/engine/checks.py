"""Engine system checks (run on `manage.py check` and before every command, so
they surface at `up`). Registered from EngineConfig.ready()."""

from typing import Any

from django.conf import settings
from django.core.checks import Warning as CheckWarning
from django.core.checks import register


@register()
def engine_model_configured(app_configs: Any = None, **kwargs: Any) -> list[CheckWarning]:
    """Warn (don't error) when ENGINE_MODEL is unset: the stack still boots so the
    operator can explore/seed, but semantic-filter runs can't judge without a
    model. A WARNING, not an Error, so it never blocks boot."""
    if settings.ENGINE_MODEL:
        return []
    return [
        CheckWarning(
            "ENGINE_MODEL is not set; semantic-filter actions can't run until it is.",
            hint=(
                "Set ENGINE_MODEL to a model your LLM serves. List a backend's models with: "
                "uv run --package openmagpie-core python -m engine.scripts.probe <ENGINE_BASE_URL> [api_key]"
            ),
            id="engine.W001",
        )
    ]
