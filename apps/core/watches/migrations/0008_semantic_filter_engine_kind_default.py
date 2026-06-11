"""One-shot data fixup for the engine generalization (OpenAI-compatible).

The relevance engine dropped the Ollama-native `OllamaEngine` (kind "ollama")
for a single `OpenAICompatEngine`; "ollama" is no longer a registered kind. A
semantic_filter action whose stored `config.engine.kind == "ollama"` (created on
the old code) would otherwise ERROR every run with "unknown engine kind". Rewrite
those pins to "" (use the server default), which now resolves to the openai_compat
engine. Only "ollama" is touched; any other kind is left for policy to flag.

Idempotent: re-running finds nothing once rewritten.
"""

from django.db import migrations


def _default_dropped_engine_kind(apps, schema_editor):
    WatchAction = apps.get_model("watches", "WatchAction")
    for action in WatchAction.objects.filter(kind="semantic_filter"):
        engine = action.config.get("engine") if isinstance(action.config, dict) else None
        if isinstance(engine, dict) and engine.get("kind") == "ollama":
            engine["kind"] = ""
            action.save(update_fields=["config"])


class Migration(migrations.Migration):
    dependencies = [
        ("watches", "0007_watch_action_delivery"),
    ]

    # Reverse is a no-op: "ollama" isn't a kind anymore, so restoring it would
    # only re-break the action; "" already resolves to the right engine.
    operations = [
        migrations.RunPython(_default_dropped_engine_kind, migrations.RunPython.noop),
    ]
