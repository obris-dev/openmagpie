"""The `--columns`-family option declarations for the list views.

The family (columns / transpose / print-columns / per-row --jsonl / list --output)
is declared ONCE here, each factory baking in the view's noun for readable help.
They return a fresh typer.OptionInfo used as the parameter default
(`columns: str | None = _columns_option()`); a factory (not a shared constant)
hands each command its own OptionInfo, so there's no shared-mutable-default trap.
"""

from __future__ import annotations

from typing import Any

import typer


def _columns_option(example: str = "") -> Any:
    """`--columns`. `example` appends a view-specific dot-path sample to the help."""
    eg = f" e.g. `{example}`;" if example else ""
    return typer.Option(
        None,
        "--columns",
        help=(
            "Columns as dot-paths into the row JSON (`--print-columns` to list them), comma-separated;"
            f"{eg} `HEADER:path` renames a column, a missing path shows `-`. Omit for the default view."
        ),
    )


def _transpose_option(noun: str) -> Any:
    """`--transpose` (psql `\\x`-style vertical, untruncated)."""
    return typer.Option(False, "--transpose", help=f"Render each {noun} vertically (full values, not truncated).")


def _print_columns_option(noun: str) -> Any:
    """`--print-columns` (list the available dot-paths from a sample row, then exit).
    The sample is the first row of the CURRENT view, so an active filter (e.g.
    `--state`) narrows it; filter to nothing and there is nothing to sample."""
    return typer.Option(
        False,
        "--print-columns",
        help=f"List the available column dot-paths (sampled from the first {noun} in the current view) and exit.",
    )


def _jsonl_rows_option(noun: str) -> Any:
    """`--jsonl` in its per-row list form (one NDJSON object per row)."""
    return typer.Option(False, "--jsonl", help=f"Emit one JSON object per {noun} (NDJSON) instead of a table.")


def _list_output_option(*, paginated: bool) -> Any:
    """`--output`/`-o`; paginated lists add the next-cursor-on-stdout note."""
    tail = "; the next cursor prints to stdout" if paginated else ""
    return typer.Option(None, "--output", "-o", help=f"Write to a file instead of stdout{tail}.")
