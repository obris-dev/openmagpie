"""Client-side StrEnum validation for filter options."""

from __future__ import annotations

from enum import StrEnum

import typer

from openmagpie_schema.watch_enums import choices


def _as_enum[E: StrEnum](value: str, enum: type[E]) -> E | None:
    """Parse a string into its StrEnum member, or None if it isn't one. The
    shared 'known value?' primitive: `_check_choice` turns a None into a rejected
    BadParameter (the value is USER INPUT), while a classifier like the run
    formatter lookup falls back to a default on None (the value is SERVER-supplied
    and a newer kind the build simply doesn't render specially)."""
    try:
        return enum(value)
    except ValueError:
        return None


def _check_choice(value: str | None, enum: type[StrEnum]) -> None:
    """Validate an optional filter value against a StrEnum client-side, raising
    typer.BadParameter (listing the choices) before the round-trip. The server
    validates these too ; this just makes the error immediate and uniform across
    the observability filters (`--state`, `--window`) instead of one being
    checked locally and another only server-side."""
    if value is not None and _as_enum(value, enum) is None:
        raise typer.BadParameter(f"{value!r}; choose from {choices(enum)}")
