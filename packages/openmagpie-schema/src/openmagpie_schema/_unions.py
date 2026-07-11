"""Shared helpers for the extensible, kind-keyed discriminated unions.

Two `kind`-keyed union families are extensible so a fork can add a typed member
without editing this package: watch-action nodes/runs (`watch/_nodes.py`,
`watch/_runs.py`) and source specs (`configs.py`). Each keeps its built-ins in a
discriminated union and falls through (left-to-right) to a plugin member whose
`kind` is any NON-built-in string:

    _Builtins = Annotated[A | B | ..., Field(discriminator="kind")]
    Union = Annotated[_Builtins | PluginMember, Field(union_mode="left_to_right")]

The plugin member MUST reject built-in kinds (via `reject_builtin_kind`), or a
built-in row with a malformed payload would be silently absorbed as a raw blob on
the fallback instead of degrading through its own typed member. The member classes
themselves differ per family (different base fields + payload), so this module
shares only the validator + the assembly discipline, not the members.

Pure Pydantic: no Django, and it imports only `pydantic` + typing, so it sits below
every model module in the import graph (members are passed in by the caller).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, get_args

# Max length of every `kind` discriminator, in ONE place: the wire contract (this pure
# package) owns it, the plugin union members constrain their `kind` to it (so the ceiling
# is encoded in the generated JSON Schema / zod for clients), AND core's kind columns
# derive their `max_length` from it (common.models re-exports this). Keeping it here, not
# in core, is what lets the client artifacts carry the bound without duplicating a literal.
KIND_MAX_LENGTH = 32


def builtin_union_kinds(builtin_union: object) -> frozenset[str]:
    """The set of `kind` Literal values across a built-in discriminated union's members
    (each member's `kind` is a single-value Literal). Derives the kind set the fallback
    rejects, so it can't drift from what the union dispatches on. Used by ALL THREE
    built-in unions (the action + run unions in `watch/`, and the source union in
    `configs.py`); the source union adds one source-specific piece on top, the
    SOURCE_KIND cross-pin.

    A `kind` Literal may hold an enum member (the action unions use
    `Literal[WatchActionKind.LOG]`) or a plain string; normalize an enum to its value so
    the returned set compares equal to a plain-string kind set regardless of whether the
    enum happens to be a StrEnum."""
    members = get_args(get_args(builtin_union)[0])
    return frozenset(
        (value.value if isinstance(value, Enum) else value)
        for member in members
        for value in get_args(member.model_fields["kind"].annotation)
    )


def reject_builtin_kind(kind: str, builtin_kinds: frozenset[str]) -> str:
    """Validator body for a plugin union member's `kind`: pass an unknown kind,
    reject a built-in kind (which must validate as its own typed member). Each
    family builds `builtin_kinds` from its own source of truth (the action enum,
    or the source union members' `SOURCE_KIND` ClassVars).

    This exclusion (and the whitespace check below) is enforced server-side (and in
    the CLI, which shares this package) but is NOT mirrored in the generated web zod:
    json-schema-to-zod can't express "string that is not one of these / must be
    trimmed" without dropping the string/min-length constraints, so the emitted plugin
    member validates `kind` as a bare `z.string().min(1)`. The gap is benign: the
    server is authoritative for writes and rejects these on the fallback, it never
    EMITS them (a corrupt built-in config degrades to null), and the web doesn't parse
    these unions at runtime yet. So the web schema accepts, but the server rejects, both
    a built-in kind with an object-shaped bad config AND a whitespace-padded kind (e.g.
    "log "); see the pinned smoke checks."""
    # `min_length=1` on the field lets a whitespace-only OR whitespace-padded kind
    # through; reject both (a kind must be an exact token that can name a registration,
    # and padding like " log " must not sneak a disguised built-in past the check
    # below). Reject rather than silently strip, so the stored kind is what was sent.
    if kind != kind.strip():
        raise ValueError("kind must not be blank or padded with whitespace")
    if kind in builtin_kinds:
        raise ValueError(f"{kind!r} {BUILTIN_KIND_REJECTION_HINT}")
    return kind


# Internal loc/msg noise the left-to-right extensible unions produce, stripped from
# operator-facing errors by `clean_union_errors`:
#  - a `tagged-union[...]` loc segment (the built-in discriminated-union branch marker),
#  - a plugin fallback member's class name in the loc (the branch label; plain strings
#    here since this module sits below the member modules in the import graph, kept in
#    sync with the Plugin* member classes),
#  - the plugin fallback's built-in-kind rejection (an internal invariant, never the
#    operator's mistake), detected by this message hint (must match the raise above).
_TAGGED_UNION_LOC_PREFIX = "tagged-union["
_PLUGIN_MEMBER_LOC_NAMES = frozenset({"PluginActionWire", "PluginActionInput", "PluginRunWire", "PluginSourceSpec"})
BUILTIN_KIND_REJECTION_HINT = "is a built-in kind; it must validate as its typed union member"


def _is_union_marker(seg: Any) -> bool:
    """Whether a loc segment is an extensible-union branch marker: the built-in
    discriminated-union label (`tagged-union[...]`) or a plugin-member class name."""
    return isinstance(seg, str) and (seg.startswith(_TAGGED_UNION_LOC_PREFIX) or seg in _PLUGIN_MEMBER_LOC_NAMES)


def _union_prefix(loc: Sequence[Any]) -> tuple[Any, ...]:
    """The loc segments before the first extensible-union marker (a `tagged-union[...]`
    segment or a plugin-member name): which union INSTANCE an error belongs to, e.g.
    `(1, 'spec')` for `sources[1].spec`, or `()` for a single top-level union."""
    prefix: list[Any] = []
    for seg in loc:
        if _is_union_marker(seg):
            break
        prefix.append(seg)
    return tuple(prefix)


def clean_union_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Strip extensible-union internals from a pydantic `errors()` list so an operator
    sees per-field paths, not union machinery. Generic over the action + source unions;
    used on the CLI + server + is the single filter the action CLI path converges on.

    Which branch's errors are the "real" ones depends on the kind, inferred from the
    errors themselves (no kind arg): the fallback's built-in-kind REJECTION is present
    iff the submitted kind is a built-in, in which case the TYPED branch is authoritative
    and the whole fallback branch is noise. Crucially this is decided PER UNION INSTANCE
    (by loc prefix), so in a multi-row list (e.g. `sources[]`) a malformed built-in in
    one row doesn't suppress a sibling row whose only error is on its fallback branch. So:
      - drop the built-in discriminator's tag errors (`union_tag_invalid` /
        `union_tag_not_found`) a non-matching kind triggers on the built-in branch (the
        other branch carries the real error for that row, so nothing is swallowed);
      - for a BUILT-IN kind at a given prefix, drop that prefix's fallback-branch errors
        (the rejection line AND its shared-field duplicates like a `rank` the typed
        branch already reported);
      - strip `tagged-union[...]` and plugin-member-name segments from surviving `loc`s,
        so `sources[0].spec.tagged-union[...].rss.url` reads `sources[0].spec.rss.url`
        and `PluginActionInput.config.1` reads `config.1`."""
    builtin_prefixes = {
        _union_prefix(err["loc"]) for err in errors if BUILTIN_KIND_REJECTION_HINT in str(err.get("msg", ""))
    }
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        loc = tuple(err["loc"])
        # Drop the built-in discriminator's tag error ONLY when the marker is the LAST loc
        # segment: that means the error is governed by OUR extensible union itself (its
        # discriminator failed at the boundary, nothing descended past it), where the tag
        # error is noise (the fallback branch carries the real error). Scope it tightly,
        # because this filter is wired into the codebase-wide DRF mapper:
        #  - a PLAIN Field(discriminator=...) union has no marker at all -> keep (else a
        #    400 with an empty body), and
        #  - a FORK typed member whose config NESTS a plain discriminated union produces a
        #    genuine tag error DEEP in the loc; that loc still carries the outer union's
        #    `tagged-union[...]` prefix, so `any(marker in loc)` would wrongly drop it. The
        #    marker isn't last there (a config path follows), so this keeps it.
        if err.get("type") in ("union_tag_invalid", "union_tag_not_found") and loc and _is_union_marker(loc[-1]):
            continue
        prefix = _union_prefix(loc)
        boundary = len(prefix)
        # A member name counts as the fallback marker ONLY at the union boundary
        # (loc[boundary]), never anywhere in the loc: a typed-branch error carries a
        # `tagged-union[...]` marker at the boundary (before any config path), so a
        # user-supplied config key that happens to equal a member class name lives
        # DEEPER and must not be misread as the fallback branch (which would drop a real
        # error) nor stripped from the rendered path.
        marker = loc[boundary] if boundary < len(loc) else None
        from_fallback = isinstance(marker, str) and marker in _PLUGIN_MEMBER_LOC_NAMES
        if from_fallback and prefix in builtin_prefixes:
            continue
        stripped = tuple(
            seg
            for i, seg in enumerate(loc)
            if not (
                isinstance(seg, str)
                and (seg.startswith(_TAGGED_UNION_LOC_PREFIX) or (i == boundary and seg in _PLUGIN_MEMBER_LOC_NAMES))
            )
        )
        cleaned.append({**err, "loc": stripped})
    return cleaned
