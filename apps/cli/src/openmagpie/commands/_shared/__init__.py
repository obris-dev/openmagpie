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
"""

from .authoring import (
    FORMAT_CHOICES,
    _abort_unexpected,
    _check_format,
    _emit_doc,
    _parse_yaml_or_abort,
)
from .choices import _as_enum, _check_choice
from .errors import _flatten_errors, _handle_api_errors, _print_api_error
from .files import _maybe_to_file, _open_editor_or_abort, _read_file_or_abort
from .output import (
    _emit_collection,
    _emit_detail,
    _emit_list,
    _Page,
    _print_detail,
    _print_next_page,
)

__all__ = [
    "FORMAT_CHOICES",
    "_Page",
    "_abort_unexpected",
    "_as_enum",
    "_check_choice",
    "_check_format",
    "_emit_collection",
    "_emit_detail",
    "_emit_doc",
    "_emit_list",
    "_flatten_errors",
    "_handle_api_errors",
    "_maybe_to_file",
    "_open_editor_or_abort",
    "_parse_yaml_or_abort",
    "_print_api_error",
    "_print_detail",
    "_print_next_page",
    "_read_file_or_abort",
]
