"""File / editor / stdout-redirect IO shared across commands."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TextIO

import typer

from ... import console


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        console.error(f"File not found: {path}")
        raise typer.Exit(code=1)
    return p.read_text()


def _open_editor_or_abort(seed: str) -> str:
    """Open $EDITOR (then $VISUAL, then `vi`) on `seed` and return the edited text.
    Aborts cleanly (pointing at `-f <file>`) if no editor can be launched, and
    aborts if the file comes back UNSAVED. Saving the same text is allowed - a
    no-op edit; only "never saved" is rejected.

    "Was it saved?" is content-first: text differing from the seed is a definite
    yes. Identical text is ambiguous - a real no-op save, or an editor that
    returned without writing (a `code`/`gedit`/`subl` launched WITHOUT its wait
    flag returns immediately) - so mtime breaks the tie. Content-first avoids the
    mtime-only false-positive on coarse-resolution filesystems (a fast
    same-second save). On the unsaved path we tell the user to make the editor
    wait rather than silently applying the unedited seed.

    The seed lives in a private (0700) temp DIRECTORY so editor swap/backup
    siblings (`x~`, `.x.swp`, `#x#`) are contained and removed on exit, and the
    file is 0600 - it can briefly hold operator-typed secrets in the add flow.

    Implemented with stdlib rather than click/typer's `edit`: Typer vendored a
    slim Click in 0.26 that dropped `edit`, and there is no standalone `click`."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "config.yaml")
        # O_EXCL + 0o600 at creation: the file never has a wider-perms window
        # (the 0700 dir already blocks traversal; this closes the gap too).
        with os.fdopen(os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
            fh.write(seed)
        before = os.path.getmtime(path)
        # Tell the user we're blocked on the editor - otherwise a launched GUI
        # editor just leaves the terminal hanging with no hint to save + close.
        typer.echo(f"Opening {editor} (save and close it to continue)...", err=True)
        try:
            subprocess.run([*shlex.split(editor), path], check=True)  # editor command from operator env
        except (OSError, subprocess.CalledProcessError) as exc:
            console.error(f"Couldn't open an editor ({editor}): {exc}. Pass the config with -f <file> instead.")
            raise typer.Exit(code=1) from None
        # The read + mtime can fail if the editor removed/renamed the file out
        # from under us (crash mid rename-over, an unlink-on-close editor).
        try:
            with open(path, encoding="utf-8") as fh:
                edited = fh.read()
            unsaved = edited == seed and os.path.getmtime(path) == before
        except OSError as exc:
            console.error(f"Couldn't read the edited file ({exc}). Pass the config with -f <file> instead.")
            raise typer.Exit(code=1) from None
        if unsaved:
            console.warn(
                "Editor closed without saving - nothing applied. "
                "A GUI editor must block until you close the file: set e.g. "
                "EDITOR='code --wait' (VS Code), 'subl -w', 'gedit -w'. Or use -f <file>."
            )
            raise typer.Exit(code=1) from None
        return edited


@contextlib.contextmanager
def _maybe_to_file(output: str | None, *, newline: str | None = None) -> Iterator[TextIO]:
    """The single seam that turns `-o <file>` into 'write there instead of stdout'.
    `yield`s the stream to write to: the open file when `output` is set, else
    `sys.stdout`. ALSO redirects `sys.stdout` to that file for the duration, so the
    console.*-based renderers (table / NDJSON) -- which only ever write to stdout
    and take no handle -- land in the file transparently. So a caller either takes
    the yielded stream for an explicit writer (`as fh: csv.writer(fh)`) or ignores
    it and writes via `console.*`; both end up in the same place.

    `newline` is passed straight to `open`: the default (None) keeps universal
    newline translation for line-oriented writers (tables / NDJSON). The CSV writer
    MUST pass `newline=""` so the csv module owns line endings - else a quoted
    field's embedded newline won't round-trip and Windows doubles the CR."""
    if output is None:
        yield sys.stdout
        return
    try:
        # encoding pinned (not the locale default): the export carries unicode
        # (LLM-extracted fields) and --jsonl emits non-ASCII (ensure_ascii=False), so
        # a non-UTF-8 box would otherwise raise mid-stream or mojibake.
        with open(output, "w", encoding="utf-8", newline=newline) as fh, contextlib.redirect_stdout(fh):
            yield fh
    except OSError as exc:
        console.error(f"failed to write {output}: {exc}")
        raise typer.Exit(code=1) from None
