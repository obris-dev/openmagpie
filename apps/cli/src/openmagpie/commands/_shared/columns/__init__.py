"""The `--columns` mechanic: dot-path projection tables, shared across list views.

Each list view renders a table that is a thin projection of the same per-row JSON
record `--jsonl` emits; `--print-columns` discovers the paths, `--transpose` is the
wide vertical view. The package, by concern:

    extract.py  pull cell values from a record (parse token, walk path, format leaf)
    options.py  the --columns-family flag declarations
    render.py   turn records into the chosen output (table / NDJSON / paths)

This `__init__` only re-exports the flat surface the views import via `_shared`.
"""

from __future__ import annotations

from .extract import _Col, _ts, col
from .options import (
    _columns_option,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _transpose_option,
)
from .render import _emit_columns_items, _emit_columns_paginated

__all__ = [
    "_Col",
    "_columns_option",
    "_emit_columns_items",
    "_emit_columns_paginated",
    "_jsonl_rows_option",
    "_list_output_option",
    "_print_columns_option",
    "_transpose_option",
    "_ts",
    "col",
]
