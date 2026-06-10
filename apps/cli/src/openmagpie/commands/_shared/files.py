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
    the edited text. Aborts cleanly (pointing at `-f <file>`) if no editor can be
    launched, and aborts if the file comes back UNSAVED. Saving the same text is
    allowed - a no-op edit; only "never saved" is rejected.

    The unsaved check (compare mtime, like git/click) is also what catches a
    non-blocking GUI editor: `code`/`gedit`/`subl` launched WITHOUT their wait
    flag return immediately, so the file is untouched - we abort and tell the user
    to make the editor wait instead of silently applying the unedited seed.

    Implemented with stdlib rather than click/typer's `edit`: Typer vendored a
    slim Click in 0.26 that dropped `edit`, and there is no standalone `click`."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(seed)
        path = fh.name
    try:
        before = os.path.getmtime(path)
        # Tell the user we're blocked on the editor - otherwise a launched GUI
        # editor just leaves the terminal hanging with no hint to save + close.
        typer.echo(f"Opening {editor} (save and close it to continue)...", err=True)
        try:
            subprocess.run([*shlex.split(editor), path], check=True)  # editor command from operator env
        except (OSError, subprocess.CalledProcessError) as exc:
            console.error(f"Couldn't open an editor ({editor}): {exc}. Pass the config with -f <file> instead.")
            raise typer.Exit(code=1) from None
        if os.path.getmtime(path) == before:
            console.warn(
                "Editor closed without saving - nothing applied. "
                "A GUI editor must block until you close the file: set e.g. "
                "EDITOR='code --wait' (VS Code), 'subl -w', 'gedit -w'. Or use -f <file>."
            )
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
