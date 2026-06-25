"""Dot-path projection: the pure value layer behind the `--columns` mechanic.

A list view's table is a thin projection of the SAME per-row JSON record `--jsonl`
emits (for activity: `{run, feed_item, feed, action}`). A `--columns` token is a
dot-path into that record - `feed_item.url`, `run.result.score` - so the table can
show anything jsonl can, and an absent path renders `-` (no per-kind mapping to
drift). `HEADER:path` renames; otherwise the header is the last segment uppercased.

Cells render by ACTUAL value type, uniform across default and user columns: a list
of scalars joins with `, `, a dict / nested list dumps to compact JSON, None / ""
/ empty renders `-`, any scalar is `str`. A datetime is just a string in JSON (no
type to dispatch on), so it is NOT special-cased - a DEFAULT column opts into
seconds formatting with `fmt=_ts`, never a global string-shape guess.

Pure: parse a token, walk a path, render a leaf to a string. No output, no
pagination - that orchestration is `render.py`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

import typer

from .... import console

_MISSING = object()


class _Col(NamedTuple):
    """A resolved column: header, the dot-path it projects, an optional width cap
    (None = table default, 0 = uncapped) and an optional raw-value `fmt`. Width /
    fmt are set only on DEFAULT columns (`col(...)`); user `--columns` leave both None."""

    header: str
    path: str
    width: int | None = None
    fmt: Callable[[Any], str] | None = None


def _split_token(token: str) -> tuple[str, str]:
    """`(header, path)` for one token: bare `path` (header = last segment,
    uppercased) or `HEADER:path`. An empty side (`HEADER:` / `:path`) is a typo."""
    header, sep, path = token.partition(":")
    if sep and (not header or not path):
        side = "path" if header else "header"
        raise typer.BadParameter(f"--columns token {token!r}: empty {side} (expected `path` or `HEADER:path`).")
    if not sep:
        path, header = header, header.rsplit(".", 1)[-1].upper()
    return header, path


def col(token: str, *, width: int | None = None, fmt: Callable[[Any], str] | None = None) -> _Col:
    """A DEFAULT column spec from a `HEADER:path` (or bare `path`) token, with an
    optional width (0 = uncapped) and a raw-value `fmt` (a poll interval -> `300s`).

    Deliberately unprefixed (the lone public name in the otherwise underscore-private
    `_shared`): it's the column-spec DSL each view authors its `_*_COLUMNS` lists
    with, so `col("STATE:run.state")` reads as a literal, not a private call."""
    header, path = _split_token(token)
    return _Col(header, path, width, fmt)


def _parse_columns(selected: str | None, default: list[_Col]) -> list[_Col]:
    """The view `default`, or the user's `--columns` as bare `_Col`s. An empty /
    all-blank `--columns` is a typo, not 'no columns' - rejected."""
    if selected is None:
        return default
    cols = [_Col(*_split_token(token)) for token in (t.strip() for t in selected.split(",")) if token]
    if not cols:
        raise typer.BadParameter("--columns is empty (give at least one dot-path, or omit it for the default view).")
    return cols


def _dedupe_headers(cols: list[_Col]) -> list[_Col]:
    """Make column headers unique so a CSV / DictReader consumer can't silently
    COLLAPSE duplicates (a user's extracted field named like a fixed column, or
    two result keys that upper-case to the same header). First occurrence keeps
    its header; each later dup gets a `_2` / `_3` suffix. The dot-path each column
    projects is untouched - only the display header is disambiguated."""
    seen: set[str] = set()  # case-folded headers already EMITTED (incl. generated ones)
    out: list[_Col] = []
    for c in cols:
        header, key = c.header, c.header.casefold()
        if key in seen:
            # Bump until the generated name is itself unused -- so a literal "X_2"
            # can't silently collide with the "X_2" generated from a second "X".
            n = 2
            while f"{header}_{n}".casefold() in seen:
                n += 1
            header = f"{header}_{n}"
            key = header.casefold()
        seen.add(key)
        out.append(c if header == c.header else c._replace(header=header))
    return out


def _ts(value: Any) -> str:
    """A column `fmt` collapsing an ISO-8601 datetime string to seconds in the
    caller's LOCAL zone, else `-`. The explicit per-column opt-in for datetimes (a
    JSON datetime is just a string). This is the ONE deliberate table/`--jsonl`
    divergence: the table shows human-local time, `--jsonl` keeps the record's
    canonical UTC ISO so machine output stays unambiguous."""
    if not value:
        return console.EMPTY
    try:
        return console.timestamp(datetime.fromisoformat(value))
    except (TypeError, ValueError):
        return str(value)


def _render_leaf(value: Any) -> str:
    """Render a leaf by ACTUAL type: None / "" / empty -> `EMPTY`; a list of scalars
    -> `, ` joined; any other list / dict -> compact JSON; any other scalar -> str."""
    if value is None or value == "":
        return console.EMPTY
    if isinstance(value, list):
        if not value:
            return console.EMPTY
        if all(isinstance(v, (str, int, float, bool)) for v in value):
            return ", ".join(str(v) for v in value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value else console.EMPTY
    return str(value)


def _path_leaf(record: dict, path: str) -> Any:
    """Walk a dot-path into `record`; the raw leaf, or `_MISSING` if any segment is
    absent (or a non-dict is traversed)."""
    cur: Any = record
    for segment in path.split("."):
        if not isinstance(cur, dict) or segment not in cur:
            return _MISSING
        cur = cur[segment]
    return cur


def _cell(record: dict, column: _Col) -> str:
    """One cell: the column's `fmt` over the present leaf, else type-based
    `_render_leaf`; a missing path -> `EMPTY`."""
    leaf = _path_leaf(record, column.path)
    if column.fmt is not None and leaf is not _MISSING and leaf is not None:
        return column.fmt(leaf)
    return console.EMPTY if leaf is _MISSING else _render_leaf(leaf)


def _flatten_paths(record: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten `record` to `(dot-path, sample)` pairs in order (gron-style): a
    non-empty dict recurses, every other value is a leaf rendered as a cell would."""
    out: list[tuple[str, str]] = []
    for key, value in record.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            out.extend(_flatten_paths(value, path))
        else:
            out.append((path, _render_leaf(value)))
    return out


def _record_line(record: dict) -> str:
    """One record as a compact, raw-UTF-8 NDJSON line (compatible with
    `model_dump_json` for these models; stdlib json and pydantic can diverge on
    floats / custom serializers, but these payloads carry neither)."""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
