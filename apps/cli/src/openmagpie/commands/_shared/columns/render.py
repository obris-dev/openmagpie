"""Render a column projection to the chosen output: table / transpose / NDJSON /
path-listing, with file redirect and pagination.

Two entry points, by data source:

    _emit_columns_paginated(fetch=, after=, limit=, ...)   cursor pages
    _emit_columns_items(items=, ...)                       a bounded in-hand list

`record_of` builds each row dict (the single source for the table AND `--jsonl`):
it takes `(item, response)` in the paginated case, so a view can join off the page
(activity's `{run, feed_item, feed, action}`), and `(item)` in the collection case.
`default_columns` is a fixed `list[_Col]`, or - paginated only - a `(response) ->
list[_Col]` callable so the column SET can depend on the typed response (activity
shows SCORE only for a scoring action, off `resp.action.kind`). The pure projection
(parse token, walk path, render leaf) is `extract.py`; the flags are `options.py`.
"""

from __future__ import annotations

import csv as _csv
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import typer

from .... import console
from ..files import _maybe_to_file
from ..output import _emit_list
from .extract import _cell, _Col, _dedupe_headers, _flatten_paths, _parse_columns, _record_line

_TRUNCATED_HINT = "Use --transpose for the full, untruncated view"

# Cells a spreadsheet (Excel / Google Sheets) evaluates as a FORMULA when a CSV
# is opened: a leading =, +, -, @, or tab/CR. Our cells carry untrusted
# third-party text (item titles + urls, LLM-extracted fields), so the CSV export
# is the classic formula-injection vector.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _is_numeric(cell: str) -> bool:
    """True if `cell` is a plain number (int/float, incl. a leading sign / exponent)."""
    try:
        float(cell)
    except ValueError:
        return False
    return True


def _csv_safe(cell: str) -> str:
    """Neutralize CSV formula injection: prefix a formula-triggering cell with a
    single quote so the spreadsheet shows it as literal text. CSV ONLY -- NDJSON
    stays byte-exact (a JSON consumer never evaluates a cell as a formula).

    Two carve-outs that would otherwise corrupt real values:
    - the empty-cell sentinel (`console.EMPTY` == "-"): our own marker, not data,
      so quoting it (-> "'-") would mangle every absent cell.
    - a sign-led NUMBER (-5, +0.83): genuine data, and a number can't be a formula,
      so it stays exact. A `-`/`+` cell that ISN'T numeric (e.g. "-1+cmd(...)") is a
      real injection vector and still gets quoted. `=` / `@` / tab / CR are never
      numbers, so they're always neutralized."""
    if cell == console.EMPTY:
        return cell
    if not cell.startswith(_CSV_FORMULA_PREFIXES):
        return cell
    if cell[0] in "+-" and _is_numeric(cell):
        return cell
    return "'" + cell


class _ItemsResponse(Protocol):
    """A paginated list response: the page's rows + the next-page cursor."""

    items: list[Any]
    next_cursor: str | None


def _validate_column_flags(columns: str | None, transpose: bool, print_columns: bool, jsonl: bool) -> None:
    """Reject contradictory flag combos up front (loudly, like a bad token)."""
    if print_columns and (columns is not None or transpose or jsonl):
        raise typer.BadParameter(
            "--print-columns lists the available paths and exits; it doesn't combine with "
            "--columns / --transpose / --jsonl."
        )
    if jsonl and (columns is not None or transpose):
        raise typer.BadParameter(
            "--columns / --transpose shape the table; --jsonl emits the full record (shape it with jq)."
        )


def _resolve_columns(
    columns: str | None,
    default_columns: list[_Col] | Callable[[list[dict]], list[_Col]],
    records: list[dict],
) -> list[_Col]:
    """The column list for an export: the view's `default_columns` (a fixed list,
    or a callable resolved from `records` when the columns depend on the data, e.g.
    expanding `result.extracted.*`) with any `--columns` override applied, then
    deduped so a CSV header can't silently collapse two same-named columns."""
    base = default_columns(records) if callable(default_columns) else default_columns
    return _dedupe_headers(_parse_columns(columns, base))


def _write_csv_header(
    writer: Any,
    columns: str | None,
    default_columns: list[_Col] | Callable[[list[dict]], list[_Col]],
    records: list[dict],
) -> list[_Col]:
    """Resolve the export columns and write the CSV header row; return the cols
    (reused for the data rows). One place for both header writes -- the first page
    with rows, and the empty-export header-only case -- so they can't diverge."""
    cols = _resolve_columns(columns, default_columns, records)
    writer.writerow([_csv_safe(c.header) for c in cols])
    return cols


def _emit_columns_stream(
    *,
    pages: Iterable[list[Any]],
    record_of: Callable[[Any], dict],
    default_columns: list[_Col] | Callable[[list[dict]], list[_Col]],
    columns: str | None,
    jsonl: bool,
    output: str | None,
) -> int:
    """Stream a paginated collection to NDJSON (`jsonl`) or CSV, PAGE BY PAGE, so
    memory stays at ~one page no matter the total (the export path; contrast
    `_emit_columns_items`, which buffers an in-hand list for the aligned table).
    `pages` is an iterable of item lists; `record_of` builds each row dict. Returns
    the number of rows written (the header is not counted).

    CSV columns resolve ONCE (from `default_columns`, with any `--columns` override)
    and reuse for every page; a deterministic kind's columns ignore the data, while
    the unknown/future-kind fallback derives them from the FIRST page only (a key
    absent there renders empty for later pages). Memory stays at ~one page because
    records are never accumulated, and each page is flushed as it's written, so a
    long export grows progressively in the file instead of all appearing at the end."""
    _validate_column_flags(columns, transpose=False, print_columns=False, jsonl=jsonl)
    written = 0
    cols: list[_Col] = []
    header_written = False  # CSV: resolve + write the header ONCE, off the first page with rows
    # newline="" so the csv module owns line endings (no \r\r\n on Windows, and a
    # quoted field's embedded newline round-trips); harmless for the NDJSON branch.
    # `stream` is the file (with -o) or stdout; the CSV writer takes it directly,
    # while console.jsonl writes via the stdout redirect _maybe_to_file also sets up.
    with _maybe_to_file(output, newline="") as stream:
        writer = None if jsonl else _csv.writer(stream)
        for page in pages:
            records = [record_of(item) for item in page]
            written += len(records)
            if jsonl:
                console.jsonl(_record_line(record) for record in records)
            else:
                assert writer is not None  # set whenever not jsonl
                if not header_written:
                    cols = _write_csv_header(writer, columns, default_columns, records)
                    header_written = True
                writer.writerows([_csv_safe(_cell(record, c)) for c in cols] for record in records)
            stream.flush()  # land each page on disk now, so a long export grows progressively
        # CSV mode where the header was never written -- i.e. `pages` yielded NO
        # pages at all (a page that's merely empty still writes a header in the loop
        # above). Emit just the header so an empty export is a valid columns-defined
        # CSV, not a 0-byte file. `writer` already targets the output.
        if not jsonl and not header_written:
            assert writer is not None  # not jsonl -> writer is set
            _write_csv_header(writer, columns, default_columns, [])
    return written


def _print_columns_and_exit(sample: dict | None, output: str | None) -> None:
    """Serve `--print-columns`: list the dot-paths of one sample record (honoring
    `-o`), or a 'nothing yet' note, then exit."""
    with _maybe_to_file(output):
        if sample is not None:
            _print_available_paths(sample)
        else:
            console.log("Nothing to sample column paths from yet.")
    raise typer.Exit()


def _print_available_paths(record: dict) -> None:
    """List a sample record's dot-paths + a short sample value each, so a user copies
    an exact `--columns` path instead of hand-tracing `--jsonl`. The intro label goes
    to stderr so `--print-columns -o file` leaves a clean path table in the file."""
    columns: list[console.Column[tuple[str, str]]] = [
        console.Column("COLUMN (dot-path)", lambda pair: pair[0], width=0),
        console.Column("SAMPLE", lambda pair: pair[1], width=50),
    ]
    console.header("Available columns (pass any as --columns; HEADER:path to rename):", err=True)
    console.table(_flatten_paths(record), columns)


def _render_record_table(
    records: list[dict], columns: str | None, transpose: bool, default: list[_Col], empty_msg: str
) -> None:
    """Render pre-built records as a dot-path table (or `--transpose`), or the
    empty-state message. `default` is an already-resolved column list."""
    if not records:
        console.log(empty_msg)
        return
    cols = _parse_columns(columns, default)
    if transpose:
        _transpose(records, cols)
    else:
        console.table(records, _table_columns(cols), note_if_truncated=_TRUNCATED_HINT)


def _transpose(records: list[dict], cols: list[_Col]) -> None:
    """Render each record VERTICALLY (psql `\\x`): aligned `LABEL  value` lines, one
    record per stanza. Values are FULL (nothing truncates); the wide-data view."""
    label_w = max((len(c.header) for c in cols), default=0)
    for index, record in enumerate(records):
        if index:
            console.log("─" * (label_w + 8))
        for c in cols:
            console.log(f"{c.header.ljust(label_w)}  {_cell(record, c)}")


def _table_columns(cols: list[_Col]) -> list[console.Column[dict]]:
    """Adapt the resolved `_Col`s to `console.Column`s (cell via `_cell`, at the
    column's width: None = default cap, 0 = uncapped)."""
    # Bind `c` per iteration (avoid the late-binding closure trap).
    return [console.Column(c.header, (lambda spec: lambda rec: _cell(rec, spec))(c), width=c.width) for c in cols]


def _emit_columns_paginated(
    *,
    fetch: Callable[[str | None, int | None], _ItemsResponse],
    after: str | None,
    limit: int | None,
    record_of: Callable[[Any, Any], dict],
    default_columns: list[_Col] | Callable[[Any], list[_Col]],
    columns: str | None,
    transpose: bool,
    print_columns: bool,
    jsonl: bool,
    output: str | None,
    empty_msg: str,
) -> None:
    """Emit a cursor-paginated list (validate flags, maybe print-columns and exit,
    else page). `record_of(item, response)`; `default_columns` resolved per page."""
    _validate_column_flags(columns, transpose, print_columns, jsonl)

    def _cols(resp: Any) -> list[_Col]:
        return default_columns(resp) if callable(default_columns) else default_columns

    if print_columns:
        # Sample the first row of the CURRENT view (honoring --after + the view's
        # own filters, e.g. --state) at limit=1 - never pull a full page.
        page = fetch(after, 1)
        _print_columns_and_exit(record_of(page.items[0], page) if page.items else None, output)

    def _jsonl(resp: _ItemsResponse) -> Iterable[str]:
        return (_record_line(record_of(item, resp)) for item in resp.items)

    def _table(resp: _ItemsResponse) -> None:
        _render_record_table([record_of(i, resp) for i in resp.items], columns, transpose, _cols(resp), empty_msg)

    _emit_list(
        fetch=lambda cursor: fetch(cursor, limit),
        after=after,
        render_table=_table,
        jsonl_lines=_jsonl,
        jsonl=jsonl,
        output=output,
    )


def _emit_columns_items(
    *,
    items: list[Any],
    record_of: Callable[[Any], dict],
    default_columns: list[_Col],
    columns: str | None,
    transpose: bool,
    print_columns: bool,
    jsonl: bool,
    output: str | None,
    empty_msg: str,
    header: str | None = None,
) -> None:
    """Emit a bounded in-hand collection, no cursor (validate flags, maybe
    print-columns and exit, else render). `record_of(item)`; fixed `default_columns`.
    An optional `header` (e.g. a count) prints above the human table, suppressed for
    `--jsonl` / `-o` so it can't corrupt machine output - the helper owns that
    guard, no per-view re-rolling. (CSV is the streaming `_emit_columns_stream`.)"""
    _validate_column_flags(columns, transpose, print_columns, jsonl)
    if print_columns:
        _print_columns_and_exit(record_of(items[0]) if items else None, output)
    records = [record_of(item) for item in items]
    if header is not None and not jsonl and output is None:
        console.header(header)
    with _maybe_to_file(output):
        if jsonl:
            console.jsonl(_record_line(record) for record in records)
        else:
            _render_record_table(records, columns, transpose, default_columns, empty_msg)
