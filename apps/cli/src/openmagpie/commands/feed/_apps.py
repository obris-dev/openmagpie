"""The typer apps + shared template constant for the `feed` command group.

Split out so the verb modules (`_crud.py`, `_sources.py`, `_items.py`) register
onto these apps without importing each other (no circular import). `feed` owns
two real sub-nouns (containment): `feed source` (operator-authored config) and
`feed item` (server-produced content, read-only). They mount here so the
topology lives in one place; the verb modules only decorate.
"""

from __future__ import annotations

from importlib import resources

import typer

feed_app = typer.Typer(no_args_is_help=True)
source_app = typer.Typer(no_args_is_help=True)
item_app = typer.Typer(no_args_is_help=True)

feed_app.add_typer(
    source_app,
    name="source",
    help="The feed's source set (what it polls). Inspect, replace, or drop sources.",
)
feed_app.add_typer(
    item_app,
    name="item",
    help="The feed's items (what polling produced). Read-only: list / get.",
)

FEED_TEMPLATE_YAML = resources.files("openmagpie").joinpath("feed_template.yaml").read_text(encoding="utf-8")
