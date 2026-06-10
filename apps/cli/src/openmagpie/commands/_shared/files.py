"""File / editor / stdout-redirect IO shared across commands."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import tempfile
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
    """Open $EDITOR (then $VISUAL, then `vi`) on `seed` in a temp file and return
    the edited text. Aborts cleanly if no editor can be launched, telling the user
    to pass `-f <file>` instead. Unchanged text is allowed - re-applying the same
    config is a valid no-op edit.

    Implemented with stdlib rather than click/typer's `edit`: Typer vendored a
    slim Click in 0.26 that dropped `edit`, and there is no standalone `click`."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(seed)
        path = fh.name
    try:
        try:
            subprocess.run([*shlex.split(editor), path], check=True)  # editor command from operator env
        except (OSError, subprocess.CalledProcessError) as exc:
            console.error(f"Couldn't open an editor ({editor}): {exc}. Pass the config with -f <file> instead.")
            raise typer.Exit(code=1) from None
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.unlink(path)


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
