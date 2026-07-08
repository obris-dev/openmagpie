"""Semantic CLI output helpers.

Use these instead of `typer.secho` / `typer.echo` so the color + stream routing
is implicit in the intent, not duplicated at every call site:

  error(msg)   red,    stderr   — failure, refusal, validation error
  warn(msg)    yellow, stderr   — caution, cancellation, dry-run notice, "are you sure?"
  success(msg) green,  stdout   — operation completed (auto-prepends "✓ ")
  header(msg)  cyan,   stdout   — section title above a block of detail
  log(msg)     plain,  stdout   — neutral output (field rows, list items, body text)
  hint(msg)    cyan,   stderr   — ambient notice (e.g. the update-available nudge)

Plus small value formatters:

  active_or_paused(is_active) -> "active" | "paused"   — canonical label for
  the `is_active` flag carried on feeds + watches.
  rate(numerator, denominator) -> "X%" or "—"   — percentage label,
  rendered "—" when the denominator is zero.
  timestamp(dt) -> "YYYY-MM-DD HH:MM:SS ZONE" or "-"   — one datetime to seconds
  precision in the caller's LOCAL zone (or "-" when None), for a time column.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

import typer


def error(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)


def warn(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.YELLOW, err=True)


def success(msg: str) -> None:
    typer.secho(f"✓ {msg}", fg=typer.colors.GREEN)


def header(msg: str, *, err: bool = False) -> None:
    # err=True routes the label off stdout (e.g. above a `-o` path list) so it
    # can't pollute machine-captured output.
    typer.secho(msg, fg=typer.colors.CYAN, err=err)


def log(msg: str) -> None:
    typer.echo(msg)


def hint(msg: str) -> None:
    # An ambient, non-error notice (e.g. the update-available nudge). Cyan, stderr,
    # so it never mixes into machine-readable stdout (--jsonl / -o) or reads as a
    # failure the way warn() (yellow) would.
    typer.secho(msg, fg=typer.colors.CYAN, err=True)


# Default per-column ceiling so one wide cell (e.g. a long meta dict) can't
# blow out the line. Overridable per column via `Column.width`.
_DEFAULT_COL_WIDTH = 48

# The placeholder for an absent / empty value in any human-facing table cell or
# detail field (a missing dot-path, None, "", an empty list / dict). The ONE marker
# the CLI uses everywhere, so list and detail views read the same (see
# apps/cli/AGENTS.md). `--jsonl` emits the real null, not this.
EMPTY = "-"


@dataclass(frozen=True)
class Column[T]:
    """One list-view column: a header label + how to render its cell from a
    typed row object. `width` caps the cell width (longer values are truncated
    with an ellipsis): None uses `_DEFAULT_COL_WIDTH`; 0 means UNCAPPED (the full
    value, never truncated)."""

    label: str
    render: Callable[[T], str]
    width: int | None = None


def _truncate(value: str, cap: int) -> str:
    if cap == 0:  # explicit uncap: the full value
        return value
    if len(value) <= cap:
        return value
    if cap <= 1:  # too narrow for "value…"; the ellipsis alone would erase the value
        return value[:cap]
    return value[: cap - 1] + "…"


def table[T](rows: Iterable[T], columns: list[Column[T]], *, note_if_truncated: str | None = None) -> bool:
    """The shared list-view renderer: a labeled header + divider, then one
    aligned row per item rendered from the typed object via each column's
    `render`. Each cell is truncated to the column's width cap (per-column
    `width`, else `_DEFAULT_COL_WIDTH`) so one long value can't blow out the
    line, then columns are padded to the widest remaining cell so headers
    line up over their values. Returns whether any rows were printed, so
    callers can fall back to an empty-state message (nothing is printed for
    an empty set). When `note_if_truncated` is set and any cell was actually
    truncated, that hint prints (yellow, stderr) BELOW the table - the seam for
    'Use --transpose for the full view'. The default styling for every
    `list`-style CLI view."""
    materialized = list(rows)
    if not materialized:
        return False
    caps = [_DEFAULT_COL_WIDTH if c.width is None else c.width for c in columns]
    raw = [[c.render(row) for c in columns] for row in materialized]
    cells = [[_truncate(raw[r][i], caps[i]) for i in range(len(columns))] for r in range(len(raw))]
    truncated = any(cells[r][i] != raw[r][i] for r in range(len(raw)) for i in range(len(columns)))
    widths = [max(len(columns[i].label), *(len(r[i]) for r in cells)) for i in range(len(columns))]

    def line(values: list[str]) -> str:
        # rstrip so the trailing column carries no padding whitespace.
        return ("  " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(values))).rstrip()

    typer.secho(line([c.label for c in columns]), fg=typer.colors.CYAN, bold=True)
    # Divider aligns its `-+-` joints under the header's ` | ` separators.
    typer.secho("  " + "-+-".join("-" * w for w in widths), fg=typer.colors.CYAN)
    for row in cells:
        typer.echo(line(row))
    # The hint goes BELOW the table (the convention for next-step notices) so a
    # long list doesn't scroll it off the top; it lands just above the page
    # prompt / cursor, staying on screen.
    if note_if_truncated and truncated:
        typer.secho(note_if_truncated, fg=typer.colors.YELLOW, err=True)
    return True


def jsonl(lines: Iterable[str]) -> None:
    """Emit pre-serialized JSON strings as newline-delimited JSON on stdout, one
    object per line (the `--jsonl` machine output). The caller serializes each
    row (e.g. `model.model_dump_json()`) so this stays a pure writer ; keeping
    stdout pure NDJSON is why pagination hints go to stderr, not here."""
    for line in lines:
        typer.echo(line)


def active_or_paused(is_active: bool) -> str:
    return "active" if is_active else "paused"


def rate(numerator: int, denominator: int) -> str:
    if not denominator:
        return "—"
    return f"{100 * numerator / denominator:.0f}%"


def timestamp(dt: datetime | None) -> str:
    """One datetime to seconds precision in the caller's LOCAL time zone, or "-"
    when None. The server sends UTC (an aware datetime); `astimezone()` converts it
    to wherever the CLI runs, and `%Z` labels the zone so the reader never has to
    guess UTC vs local. A naive datetime is assumed to be local already; `%Z` is
    dropped (rstrip) on platforms that report it empty. Backs both the list time
    columns (via the columns `_ts` fmt) and the `get`/`summary` detail fields, so
    every human-facing timestamp is local; `--jsonl` keeps the canonical UTC ISO."""
    if not dt:
        return EMPTY
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z").rstrip()
