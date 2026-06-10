"""File / editor / stdout-redirect IO shared across commands."""

from __future__ import annotations

import contextlib
from pathlib import Path

import typer

from ... import console


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        console.error(f"File not found: {path}")
        raise typer.Exit(code=1)
    return p.read_text()


def _open_editor_or_abort(seed: str) -> str:
    """Open $EDITOR on `seed` (the current config for an edit). Aborts
    if the editor returns nothing (quit without saving). Unchanged text
    is allowed — re-applying the same config is a valid no-op edit."""
    edited = typer.edit(seed, extension=".yaml")
    if edited is None:
        console.warn("Edit cancelled.")
        raise typer.Exit(code=1) from None
    return edited


@contextlib.contextmanager
def _maybe_to_file(output: str | None):
    """Redirect stdout to `output` for the duration, or pass through when None.
    The page renderers stay stdout-only ; this is the single seam that turns
    `-o <file>` into 'write the rows there instead'."""
    if output is None:
        yield
        return
    try:
        with open(output, "w") as fh, contextlib.redirect_stdout(fh):
            yield
    except OSError as exc:
        console.error(f"failed to write {output}: {exc}")
        raise typer.Exit(code=1) from None
