"""Cross-command CLI plumbing, split by concern.

A package (not a module) so each concern stays a focused file under the
line-length budget; this `__init__` re-exports the flat names so call sites keep
`from .._shared import _emit_list` unchanged. Leading underscore on each helper
keeps it internal to the commands package, not a public surface.

  errors    - transport / server error rendering at the command boundary
  files     - file / editor reads + the `-o` stdout-redirect seam
  output    - detail tables + the table/NDJSON/`-o` emit helpers (read contract)
  choices   - client-side StrEnum filter validation
  authoring - `--format` + documented-template + dry-run-contract plumbing
  timeflags - the export no-window default + client-side validation of the
              `--*-since`/`--*-until` flags (server resolves; see run_windows)
"""

from .authoring import (
    FORMAT_CHOICES,
    _abort_unexpected,
    _active_flip_note,
    _check_format,
    _emit_doc,
    _parse_yaml_or_abort,
)
from .choices import _as_enum, _check_choice
from .columns import (
    _Col,
    _columns_option,
    _emit_columns_items,
    _emit_columns_paginated,
    _emit_columns_stream,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _transpose_option,
    _ts,
    col,
)
from .errors import (
    CONTRACT_MISMATCH_MESSAGE,
    _abort_contract_mismatch,
    _abort_union_validation_error,
    _flatten_errors,
    _handle_api_errors,
    _print_api_error,
    _union_error_lines,
    _unreachable_message,
)
from .files import _maybe_to_file, _open_editor_or_abort, _read_file_or_abort
from .output import (
    _emit_detail,
    _emit_list,
    _Page,
    _print_detail,
    _print_next_page,
)
from .timeflags import _DEFAULT_WINDOW, _build_windows, validated_window_params

__all__ = [
    "CONTRACT_MISMATCH_MESSAGE",
    "FORMAT_CHOICES",
    "_DEFAULT_WINDOW",
    "_Col",
    "_Page",
    "_abort_contract_mismatch",
    "_abort_unexpected",
    "_abort_union_validation_error",
    "_active_flip_note",
    "_as_enum",
    "_build_windows",
    "_check_choice",
    "_check_format",
    "_columns_option",
    "_emit_columns_items",
    "_emit_columns_paginated",
    "_emit_columns_stream",
    "_emit_detail",
    "_emit_doc",
    "_emit_list",
    "_flatten_errors",
    "_handle_api_errors",
    "_jsonl_rows_option",
    "_list_output_option",
    "_maybe_to_file",
    "_open_editor_or_abort",
    "_parse_yaml_or_abort",
    "_print_api_error",
    "_print_columns_option",
    "_print_detail",
    "_print_next_page",
    "_read_file_or_abort",
    "_transpose_option",
    "_ts",
    "_union_error_lines",
    "_unreachable_message",
    "col",
    "validated_window_params",
]
