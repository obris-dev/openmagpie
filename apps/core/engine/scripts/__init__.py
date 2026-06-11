"""Runnable engine helper scripts (invoked via `python -m engine.scripts.<name>`),
kept apart from the engine's import surface. Standalone + Django-free so they can
run on the host before the stack is up (e.g. the quickstart's model probe)."""
