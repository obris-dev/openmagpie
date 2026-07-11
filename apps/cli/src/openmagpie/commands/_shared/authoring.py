"""Create/edit/template authoring plumbing shared by feed + watch.

The `--format` option helpers + the documented-template emitter + the dry-run
contract guards + the edit-time pause-flip warning. Byte-identical across the
feed + watch command groups (only the noun / envelope class vary), so they live
here once; the resource-specific
mutation flow (`_run_mutation` / `_print_*` / `_edit_seed`) stays per-module,
where it differs in real ways a 2-caller parameterization would only obscure.
"""

from __future__ import annotations

import json
import sys

import typer
import yaml
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from ... import console
from .errors import _abort_union_validation_error

# Shared `--format yaml|json` option helpers for the template / export commands.
# One source of truth for the accepted values and the validation message.
FORMAT_CHOICES = ("yaml", "json")


def _check_format(format: str) -> str:
    fmt = format.lower()
    if fmt not in FORMAT_CHOICES:
        console.error(f"--format must be one of {FORMAT_CHOICES!r}, got {format!r}")
        raise typer.Exit(code=1)
    return fmt


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
        # Shared union-error rendering: a multi-action `watch create` (WatchInput with an
        # actions[] extensible union) reads as per-field paths, not tagged-union noise.
        _abort_union_validation_error(e, header="Config envelope error:")


def _active_flip_note(*, current: bool, submitted: bool, noun: str, resource_id: str) -> str | None:
    """Warn when a full-replace edit (PUT) flips is_active. YAML can't tell an OMITTED
    is_active from an explicit `true` (both parse True), so an `-f` file that doesn't
    mention it silently un-pauses a paused feed/watch. Returns the warning on a
    pause-state flip (pointing at the direct verb), else None. The $EDITOR seed carries
    the current value, so this only fires on a real change. Pure, so it's unit-testable."""
    if current == submitted:
        return None
    verb = "resume" if submitted else "pause"
    return (
        f"This edit will {verb} the {noun} (is_active {current} -> {submitted}); a -f file that "
        f"omits is_active defaults it to true. To change only the pause state: magpie {noun} {verb} {resource_id}"
    )
