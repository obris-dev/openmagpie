"""`manage.py engine_status` - report the configured LLM engine's reachability +
available models, using the REAL engine (the OpenAI client, from inside the
container so it hits the LLM by the same network path a judge will). The single
in-container reachability check: the quickstart's tick step gates on the exit code
(0 = reachable, 1 = not), and an operator can run it to debug ENGINE_BASE_URL /
ENGINE_API_KEY. Read-only; never mutates anything."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from engine import registry as engine_registry


class Command(BaseCommand):
    help = "Report the configured LLM engine's reachability + available models; exit 1 if unreachable."

    def handle(self, *args: Any, **options: Any) -> None:
        # Ask the configured engine for its reachability snapshot. status() never
        # raises - it maps unreachable / auth / shape-drift into an EngineStatus
        # with a reason instead.
        engine = engine_registry.get()
        status = engine.status()
        if not status.available:
            # CommandError (not raise SystemExit): still exits 1 for tick.sh's gate,
            # but is catchable under call_command instead of tearing down a runner.
            msg = status.unreachable_reason or "the LLM is unreachable"
            if status.how_to_fix:
                msg = f"{msg}\n{status.how_to_fix}"
            raise CommandError(msg)
        models = ", ".join(status.available_models) or "(none reported)"
        self.stdout.write(f"LLM reachable; default model {status.default_model!r}; available: {models}")
