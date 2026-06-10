"""Semantic CLI output helpers.

Use these instead of `typer.secho` / `typer.echo` so the color + stream routing
is implicit in the intent, not duplicated at every call site:

  error(msg)   red,    stderr   — failure, refusal, validation error
  warn(msg)    yellow, stderr   — caution, cancellation, dry-run notice, "are you sure?"
  success(msg) green,  stdout   — operation completed (auto-prepends "✓ ")
  header(msg)  cyan,   stdout   — section title above a block of detail
  log(msg)     plain,  stdout   — neutral output (field rows, list items, body text)

Plus small value formatters:

  active_or_paused(is_active) -> "active" | "paused"   — canonical label for
  the `is_active` flag carried on feeds + watches.
  rate(numerator, denominator) -> "X%" or "—"   — percentage label,
  rendered "—" when the denominator is zero.
  timestamp(dt) -> "YYYY-MM-DD HH:MM:SS" or "-"   — one datetime to seconds
  precision (or "-" when None), for a list view's time column.
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


def header(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.CYAN)


def log(msg: str) -> None:
    typer.echo(msg)


# Default per-column ceiling so one wide cell (e.g. a long meta dict) can't
# blow out the line. Overridable per column via `Column.width`.
_DEFAULT_COL_WIDTH = 48


@dataclass(frozen=True)
class Column[T]:
    """One list-view column: a header label + how to render its cell from a
    typed row object. `width` caps the cell width (longer values are
    truncated with an ellipsis) ; None uses `_DEFAULT_COL_WIDTH`."""

    label: str
    render: Callable[[T], str]
    width: int | None = None


def _truncate(value: str, cap: int) -> str:
    return value if len(value) <= cap else value[: cap - 1] + "…"


def table[T](rows: Iterable[T], columns: list[Column[T]]) -> bool:
    """The shared list-view renderer: a labeled header + divider, then one
    aligned row per item rendered from the typed object via each column's
    `render`. Each cell is truncated to the column's width cap (per-column
    `width`, else `_DEFAULT_COL_WIDTH`) so one long value can't blow out the
    line, then columns are padded to the widest remaining cell so headers
    line up over their values. Returns whether any rows were printed, so
    callers can fall back to an empty-state message (nothing is printed for
    an empty set). The default styling for every `list`-style CLI view."""
    materialized = list(rows)
    if not materialized:
        return False
    caps = [c.width or _DEFAULT_COL_WIDTH for c in columns]
    cells = [[_truncate(c.render(row), caps[i]) for i, c in enumerate(columns)] for row in materialized]
    widths = [max(len(columns[i].label), *(len(r[i]) for r in cells)) for i in range(len(columns))]

    def line(values: list[str]) -> str:
        # rstrip so the trailing column carries no padding whitespace.
        return ("  " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(values))).rstrip()

    typer.secho(line([c.label for c in columns]), fg=typer.colors.CYAN, bold=True)
    # Divider aligns its `-+-` joints under the header's ` | ` separators.
    typer.secho("  " + "-+-".join("-" * w for w in widths), fg=typer.colors.CYAN)
    for row in cells:
        typer.echo(line(row))
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
    """One datetime to seconds precision (drops microseconds / tz) ; "-" when
    None. For a list view's time column."""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"
