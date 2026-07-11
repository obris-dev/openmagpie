"""Public helpers for operator-facing errors from the shared extensible unions.

`clean_union_errors` strips the left-to-right unions' internal noise (the
`tagged-union[...]` discriminator loc segments and the plugin fallback's built-in-kind
rejection) from a pydantic `errors()` list, so a consumer (core's DRF error mapper, the
CLI's error printer) renders per-field paths. The implementation lives in the private
`_unions` module next to `reject_builtin_kind`, whose message it must match; this module
is the public entry point for consumers outside the package.
"""

from ._unions import clean_union_errors

__all__ = ["clean_union_errors"]
