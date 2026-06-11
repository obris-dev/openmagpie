"""Read-command output: detail tables + the table/NDJSON/`-o` emit helpers.

The machine-output contract every `get`/`list` view shares (full rationale in
apps/cli/AGENTS.md): a human table by default, `--jsonl` for NDJSON, `-o <file>`
to redirect. `_emit_list` paginates; `_emit_detail` is the single-object form.
The column-aware list emit (`_emit_columns_paginated` / `_emit_columns_items`)
lives in the `columns` package and wraps `_emit_list`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from typing import Protocol

import typer

from ... import console
from .files import _maybe_to_file


def _print_detail(header: str, fields: list[tuple[str, str]]) -> None:
    """A key/value detail table (the `get` views' shape): a header line then a
    2-column FIELD / VALUE table. Shared by the activity / delivery `get`
    renderers ; a caller adds any extras (e.g. a delivery's request payload)
    after."""
    cols: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=12),
        console.Column("VALUE", lambda kv: kv[1]),
    ]
    console.header(header)
    console.table(fields, cols)


def _print_next_page(next_cursor: str | None, *, to_stderr: bool = False) -> None:
    """Print the next-page cursor hint after a paginated list, when another page
    exists. Shared by the activity / delivery `list` renderers. `to_stderr` routes
    it off stdout for `--jsonl` (so the hint can't corrupt the NDJSON stream)."""
    if next_cursor:
        typer.echo(f"\nNext page: --after {next_cursor}", err=to_stderr)


class _Page(Protocol):
    """The minimal shape `_emit_list` itself reads off a list response: the
    next-page cursor that drives the loop. The per-command renderers
    (`render_table` / `jsonl_lines`) receive the SAME concrete response and read
    the rest (`.items`, the keyed side tables) ; the generic only needs the
    cursor, so `P` binds to each command's real response type at the call site."""

    next_cursor: str | None


def _emit_list[P: _Page](
    *,
    fetch: Callable[[str | None], P],
    after: str | None,
    render_table: Callable[[P], None],
    jsonl_lines: Callable[[P], Iterable[str]],
    jsonl: bool,
    output: str | None,
) -> None:
    """Emit a paginated list in the right mode (the read output contract; full
    rationale in apps/cli/AGENTS.md). `fetch(cursor)` returns one page (`.items` +
    `.next_cursor`); `render_table` / `jsonl_lines` render one. Interactive TTY
    (and not `-o`) prompt-pages BOTH table and `--jsonl` (table pages get a
    `Page: <n>` marker; `--jsonl` stays pure NDJSON, the prompt delineating it);
    `-o` writes one page to the file + the bare next cursor on stdout (scripted
    loop); piped/non-TTY emits one page + the `--after` hint (stdout for table,
    stderr for `--jsonl`)."""
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if output or not interactive:
        resp = fetch(after)
        with _maybe_to_file(output):
            if jsonl:
                console.jsonl(jsonl_lines(resp))
            else:
                render_table(resp)
        if output:
            # Bare cursor on stdout (data went to the file) = the scripted-loop
            # contract: read it, stop on empty. Don't dress it up.
            if resp.next_cursor:
                typer.echo(resp.next_cursor)
        else:
            _print_next_page(resp.next_cursor, to_stderr=jsonl)
        return
    page = 0
    while True:
        resp = fetch(after)
        page += 1
        if jsonl:
            console.jsonl(jsonl_lines(resp))  # pure NDJSON ; no Page: marker on stdout
        else:
            console.log(f"\nPage: {page}")  # blank line, marker, then the table directly below
            render_table(resp)
        after = resp.next_cursor
        if not after:
            break
        try:
            advance = typer.confirm("Fetch next page?", default=True, err=True)
        except typer.Abort:
            # Ctrl-C / EOF at the prompt: pages shown are valid output; exit 130
            # (repo convention, leading newline off `^C`), not click's "Aborted.".
            console.warn("\nStopped.")
            raise typer.Exit(code=130) from None
        if not advance:
            break


def _emit_detail(
    *,
    render: Callable[[], None],
    json_obj: Callable[[], str],
    jsonl: bool,
    output: str | None,
) -> None:
    """Emit a single-object view (`get` / `summary`): the field tables by default,
    one JSON object with `--jsonl`, either one routed to `-o <file>`."""
    with _maybe_to_file(output):
        if jsonl:
            typer.echo(json_obj())
        else:
            render()
