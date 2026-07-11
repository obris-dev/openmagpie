"""Pydantic -> DRF error-shape conversion.

Shared by every serializer that validates a `data` blob through a
Pydantic config (feeds, watches, ...). Produces the flat
`{path: [messages]}` shape the magpie CLI error printer expects (see
cli/AGENTS.md), one key per leaf path.
"""

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from openmagpie_schema.errors import clean_union_errors


def loc_to_path(loc: tuple[Any, ...]) -> str:
    """Render a Pydantic `loc` tuple as a flat field path.

    `('streams', 0, 'spec', 'kind')` -> `streams[0].spec.kind`. Integer
    segments are list indices and become `[i]`; named segments are
    dot-joined. One key per leaf, no nested dicts (so sibling errors under
    the same parent can't collide, and array-element paths render as
    `streams[0]...`, not `streams.0...`).
    """
    parts: list[str] = []
    for seg in loc:
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            parts.append(str(seg) if not parts else f".{seg}")
    return "".join(parts) or "__root__"


def pydantic_errors_to_drf(exc: PydanticValidationError) -> dict[str, Any]:
    """Re-shape Pydantic's error list into DRF's `{path: [messages]}` dict.

    Flat, one key per leaf path (see `loc_to_path`). Multiple messages for
    the same path accumulate in the list. Runs through `clean_union_errors`
    first, so an extensible-union (action / source) failure surfaces per-field
    paths instead of internal `tagged-union[...]` prefixes + the plugin
    fallback's built-in-kind contract line.
    """
    out: dict[str, list[str]] = {}
    for err in clean_union_errors(exc.errors()):
        out.setdefault(loc_to_path(tuple(err["loc"])), []).append(err["msg"])
    return out
