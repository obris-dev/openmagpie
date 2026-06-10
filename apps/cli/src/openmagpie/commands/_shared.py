"""Cross-command CLI plumbing.

Decorator + I/O + transport-error rendering used by more than one command
module. Lives here so each command module imports from a package-private
shared module instead of reaching across to a sibling's underscore-prefixed
symbols. Leading underscore on each helper keeps them internal to the
commands package — they are not part of any public surface.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from openmagpie_schema.watch_enums import choices

from .. import console
from ..http import ApiError, AuthError


def _handle_api_errors[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Translate the transport failure modes into one clean CLI exit, at
    the command boundary. Command bodies just call `ac.api.*` directly —
    no thunks. `typer.Exit` (confirm-aborts, the persistence sanity
    guards) is NOT caught, so it propagates normally. `ApiError` goes
    through `_print_api_error` so 400 field errors and structured
    4xx/5xx details both read legibly."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return fn(*args, **kwargs)
        except AuthError:
            console.error("Not authenticated. Run `magpie auth login` first.")
            raise typer.Exit(code=1) from None
        except ApiError as e:
            _print_api_error(e)
            raise typer.Exit(code=1) from None
        except httpx.HTTPError as e:
            console.error(f"Couldn't reach the server ({type(e).__name__}).")
            raise typer.Exit(code=1) from None

    return wrapper


def _read_file_or_abort(path: str) -> str:
    p = Path(path)
    if not p.exists():
        console.error(f"File not found: {path}")
        raise typer.Exit(code=1)
    return p.read_text()


def _open_editor_or_abort(seed: str) -> str:
    """Open $EDITOR on `seed` (the current config for an edit). Aborts
    if the editor returns nothing (quit without saving). Unchanged text
    is allowed — re-applying the same config is a valid no-op edit."""
    edited = typer.edit(seed, extension=".yaml")
    if edited is None:
        console.warn("Edit cancelled.")
        raise typer.Exit(code=1) from None
    return edited


def _print_api_error(e: ApiError) -> None:
    """Pretty-print a server-side error.

    On 400 the body is the serializer's flat `{path: [messages]}` shape
    (`{"data": {"streams[0].spec.kind": ["..."]}}`); walk it and print
    one line per leaf. Non-400 (404/409/5xx) carry a structured
    `{"error","detail"}`; surface the `detail`/`error` string only —
    never the whole body, which can carry tokens on other endpoints.
    """
    if e.status == 400 and isinstance(e.body, dict):
        console.error("Validation error:")
        for line in _flatten_errors(e.body):
            console.error(f"  {line}")
        return
    detail = ""
    if isinstance(e.body, dict):
        for key in ("detail", "error"):
            val = e.body.get(key)
            if isinstance(val, str) and val:
                detail = f" {val}"
                break
    console.error(f"Server returned an error (HTTP {e.status}).{detail}")


def _flatten_errors(body: Any, prefix: str = "") -> list[str]:
    """DFS over the error dict; yield `path: message` strings.

    The value at each leaf is typically a list of strings, but the
    server also emits a top-level "detail" key for non-field errors, so
    handle scalars too.
    """
    out: list[str] = []
    if isinstance(body, dict):
        for key, val in body.items():
            child_prefix = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten_errors(val, child_prefix))
    elif isinstance(body, list):
        for i, item in enumerate(body):
            if isinstance(item, (dict, list)):
                out.extend(_flatten_errors(item, f"{prefix}[{i}]"))
            else:
                out.append(f"{prefix or '_'}: {item}")
    else:
        out.append(f"{prefix or '_'}: {body}")
    return out


def _print_detail(header: str, fields: list[tuple[str, str]]) -> None:
    """A key/value detail table (the `get` views' shape): a header line then a
    2-column FIELD / VALUE table. Shared by the activity / delivery `get`
    renderers ; a caller adds any extras (e.g. a delivery's request payload)
    after."""
    cols: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=12),
        console.Column("VALUE", lambda kv: kv[1]),
    ]
    console.header(header)
    console.table(fields, cols)


def _print_next_page(next_cursor: str | None) -> None:
    """Print the next-page cursor hint after a paginated list, when another page
    exists. Shared by the activity / delivery `list` renderers."""
    if next_cursor:
        console.log(f"\nNext page: --after {next_cursor}")


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


# Shared `--format yaml|json` option helpers for the template /
# export commands. One source of truth for the accepted values and
# the validation message, used by both `feed template[--with-source]`
# and `feed (template|export)-sources`.
FORMAT_CHOICES = ("yaml", "json")


def _check_format(format: str) -> str:
    fmt = format.lower()
    if fmt not in FORMAT_CHOICES:
        console.error(f"--format must be one of {FORMAT_CHOICES!r}, got {format!r}")
        raise typer.Exit(code=1)
    return fmt


# ── Shared create/edit (`magpie <resource> template|create|edit`) plumbing ──
# These are byte-identical across the feed + watch command groups (only the
# noun / envelope class vary), so they live here once. The resource-specific
# mutation flow (_run_mutation / _print_* / _edit_seed) stays per-module ; it
# differs in real ways (api resource, fields, output layout) that a 2-caller
# parameterization would only obscure.


def _emit_doc(yaml_text: str, *, format: str, output: str | None) -> None:
    """Write a documented YAML template verbatim (yaml; comments preserved)
    or projected through json (comments dropped; for scripted consumers)."""
    text = yaml_text if format == "yaml" else json.dumps(yaml.safe_load(yaml_text), indent=2)
    text = text if text.endswith("\n") else text + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    try:
        with open(output, "w") as fh:
            fh.write(text)
    except OSError as exc:
        console.error(f"failed to write {output}: {exc}")
        raise typer.Exit(code=1) from None
    console.success(f"Wrote template to {output}")


def _abort_unexpected(what: str, maybe_id: str | None, *, noun: str) -> typer.Exit:
    """One clean exit for a server response that doesn't match the dry-run
    contract. `noun` is the resource word (feed / watch)."""
    msg = f"Unexpected server response: {what}."
    if maybe_id:
        msg += f" A {noun} may have been created | check id {maybe_id}"
    console.error(msg)
    return typer.Exit(code=1)


def _parse_yaml_or_abort[T: BaseModel](text: str, envelope_cls: type[T]) -> T:
    """Parse a YAML config body into `envelope_cls`, with clean CLI errors
    for a non-mapping root, a YAML syntax error, or a shape mismatch."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        console.error(f"YAML parse error: {e}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.error("Config root must be a YAML mapping (key: value pairs).")
        raise typer.Exit(code=1)
    try:
        return envelope_cls.model_validate(parsed)
    except PydanticValidationError as e:
        console.error("Config envelope error:")
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            console.error(f"  {path}: {err['msg']}")
        raise typer.Exit(code=1) from None
