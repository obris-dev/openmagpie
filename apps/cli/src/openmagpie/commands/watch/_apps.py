"""The typer apps + shared template constant for the `watch` command group.

Split out so `_crud.py` and `_actions.py` both register verbs onto the
same app objects without importing each other (no circular import).
"""

from __future__ import annotations

from importlib import resources

import typer

watch_app = typer.Typer(no_args_is_help=True)
action_app = typer.Typer(no_args_is_help=True)
watch_app.add_typer(
    action_app,
    name="action",
    help="A watch's action chain: list / get / add / edit / delete (+ template).",
)

WATCH_TEMPLATE_YAML = resources.files("openmagpie").joinpath("watch_template.yaml").read_text(encoding="utf-8")
WATCH_ACTION_TEMPLATE_YAML = resources.files("openmagpie").joinpath("action_template.yaml").read_text(encoding="utf-8")
