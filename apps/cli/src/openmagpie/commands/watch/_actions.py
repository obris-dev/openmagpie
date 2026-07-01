"""`magpie watch action ...` verbs: surgical single-action chain ops.

The complement to the whole-watch YAML on `watch create`/`edit`: inspect, add,
edit, or drop one action without round-tripping the full config. `list`/`add`
take `--watch` (no action id yet); `get`/`edit`/`delete` take the action's own
id. `template` emits the starter file `add`/`edit` consume. All wrap the server's
`/v1/watches/<id>/actions` (chain list/add) and `/v1/actions/<id>` (per-action
get/edit/delete) endpoints.

The run / delivery AUDIT for an action is not here: it lives under the flat
`magpie activity` and `magpie delivery` nouns (observability is queried, not
walked through its parent chain).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

import typer
import yaml
from pydantic import ValidationError

from openmagpie_schema.watch import WatchActionMutationResponse, WatchActionWire, build_watch_action_input

from ... import console
from ...context import app_ctx
from .._shared import (
    _abort_unexpected,
    _check_format,
    _columns_option,
    _emit_columns_items,
    _emit_detail,
    _emit_doc,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _open_editor_or_abort,
    _print_columns_option,
    _print_detail,
    _read_file_or_abort,
    _transpose_option,
    col,
)
from ._apps import WATCH_ACTION_TEMPLATE_YAML, action_app

# Default `watch action list` columns, as `HEADER:dot-path` into an action record.
_ACTION_COLUMNS = [col("ID:id"), col("RANK:rank"), col("KIND:kind"), col("SUMMARY:summary.detail")]


@action_app.command("list")
@_handle_api_errors
def action_list(
    watch_id: str = typer.Option(..., "--watch", "-w", help="Watch id whose action chain to list."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("action"),
    print_columns: bool = _print_columns_option("action"),
    jsonl: bool = _jsonl_rows_option("action"),
    output: str | None = _list_output_option(paginated=False),
) -> None:
    """List a watch's action chain, in rank order."""
    _emit_columns_items(
        items=app_ctx().api.watch.list_actions(watch_id),
        record_of=lambda a: a.model_dump(mode="json"),
        default_columns=_ACTION_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No actions yet. Add one with `magpie watch action add`.",
    )


@action_app.command("get")
@_handle_api_errors
def action_get(
    action_id: str = typer.Argument(..., help="Action id (from `magpie watch action list`)."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the action as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one action's definition (kind + config) by its own id."""
    action = app_ctx().api.watch.get_action(action_id)
    _emit_detail(
        render=lambda: _print_action_detail(action), json_obj=action.model_dump_json, jsonl=jsonl, output=output
    )


def _print_action_detail(a: WatchActionWire, *, title: str | None = None) -> None:
    fields: list[tuple[str, str]] = [
        ("kind", a.kind),
        ("rank", str(a.rank)),
        ("summary", a.summary.detail or console.EMPTY),
    ]
    _print_detail(title or f"action {a.id}", fields)
    console.log("\nconfig:")  # the server-redacted config blob, in full
    # `config` is the typed union member's config model; dump to its plain dict
    # for display (the on-wire shape is JSON, secrets already redacted). A
    # corrupt-at-rest config degrades to null on the wire (config=None), so render
    # it as `null` rather than crashing on the missing model.
    config_json = a.config.model_dump(mode="json") if a.config is not None else None
    console.log(json.dumps(config_json, indent=2, sort_keys=True))
    if a.config is None:
        # Consistent with the edit paths, which flag a corrupt config rather than
        # show a bare null: say why it's null so a reader doesn't read it as "unset".
        console.warn("This action's stored config is unreadable (corrupt at rest); shown as null above.")


@action_app.command("template")
def action_template(
    format: str = typer.Option(
        "yaml",
        "--format",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter single-action file (the `{kind, config}` shape `add` / `edit` consume)."""
    fmt = _check_format(format)
    _emit_doc(WATCH_ACTION_TEMPLATE_YAML, format=fmt, output=output)


@action_app.command("add")
@_handle_api_errors
def action_add(
    watch_id: str = typer.Option(..., "--watch", "-w", help="Watch id whose chain to add to."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML/JSON action ('-' for stdin). Omit to fill in a template in $EDITOR."
    ),
    rank: int | None = typer.Option(None, "--rank", "-r", help="Insert position (0-based). Appends when omitted."),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Add one action to a watch's chain.

    One action: `{kind: <kind>, config: {...}}` (the same shape as an entry in a
    watch template's `actions:` list, or `magpie watch action template`). Omit
    `-f` to fill in the template in $EDITOR."""
    if file is None:
        text = _open_editor_or_abort(WATCH_ACTION_TEMPLATE_YAML)
    elif file == "-":
        text = sys.stdin.read()
    else:
        text = _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    api = app_ctx().api.watch
    created = _run_action_mutation(
        lambda dr: api.add_action(watch_id, kind, config, rank=rank, dry_run=dr),
        is_edit=False,
        dry_run=dry_run,
        yes=yes,
    )
    if created is not None:
        console.success(f"Added {created.kind} at rank {created.rank} ({created.id})")


@action_app.command("edit")
@_handle_api_errors
def action_edit(
    action_id: str = typer.Argument(..., help="Action id (from `magpie watch action list`)."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML/JSON action ('-' for stdin). Omit to edit the current config in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Replace one action's config in place (same position in the chain).

    One action: `{kind: <kind>, config: {...}}` ; `kind` may differ from the
    current one to swap the node's kind. Omit `-f` to edit the action's current
    config in $EDITOR (a masked secret left in place is preserved server-side)."""
    api = app_ctx().api.watch
    if file is None:
        current = api.get_action(action_id)
        # A corrupt-at-rest config degrades to null on the wire (config=None); seed
        # an empty placeholder for the operator to fill instead of crashing on the
        # None, flagging in the seed why the config came back blank.
        if current.config is None:
            config: dict[str, Any] = {}
            seed = "# NOTE: the stored config was unreadable (corrupt) and replaced with an\n"
            seed += "# empty placeholder; fill it in before applying.\n"
        else:
            config = current.config.model_dump(mode="json")
            seed = ""
        seed += yaml.safe_dump({"kind": current.kind, "config": config}, sort_keys=False)
        text = _open_editor_or_abort(seed)
    elif file == "-":
        text = sys.stdin.read()
    else:
        text = _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    updated = _run_action_mutation(
        lambda dr: api.edit_action(action_id, kind, config, dry_run=dr),
        is_edit=True,
        dry_run=dry_run,
        yes=yes,
    )
    if updated is not None:
        console.success(f"Updated action {updated.id} ({updated.kind}, rank {updated.rank})")


@action_app.command("delete")
@_handle_api_errors
def action_delete(
    action_id: str = typer.Argument(..., help="Action id (from `magpie watch action list`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Delete one action from a watch's chain by its own id (the chain renumbers
    to stay dense). The watch is resolved server-side."""
    api = app_ctx().api.watch
    action = api.get_action(action_id)  # resolve first so the confirm names what goes
    label = f"{action.kind} at rank {action.rank} ({action_id})"
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete action {label}.")
            raise typer.Exit(code=1)
        console.warn(f"Delete action {label}? The chain renumbers to stay dense.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    api.delete_action(action_id)
    console.success(f"Deleted action {label}")


def _parse_action_or_abort(text: str) -> tuple[str, dict[str, Any]]:
    """Parse a single-action file into `(kind, config)`. The expected
    shape is `{kind: <kind>, config: {...}}` ; the same shape as an entry
    in a watch template's `actions:` list, so an operator can copy one
    out and feed it here."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        console.error(f"YAML parse error: {e}")
        raise typer.Exit(code=1) from None
    if not isinstance(parsed, dict):
        console.error("Action must be a YAML mapping with `kind` and `config`.")
        raise typer.Exit(code=1)
    kind = parsed.get("kind")
    config = parsed.get("config")
    if not isinstance(kind, str) or not kind:
        console.error("Action `kind` is required (a string).")
        raise typer.Exit(code=1)
    if not isinstance(config, dict):
        console.error("Action `config` must be a mapping.")
        raise typer.Exit(code=1)
    # Validate the config against the kind's typed shape HERE, at parse time, so a
    # config typo (a missing/out-of-range field) or an unknown kind renders per-field
    # errors like it did on main. Left unvalidated it would surface later as a
    # ValidationError the command-boundary handler mislabels as a version mismatch.
    try:
        build_watch_action_input(kind=kind, config=config)
    except ValidationError as e:
        console.error("Action config error:")
        for err in e.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            console.error(f"  {path}: {err['msg']}")
        raise typer.Exit(code=1) from None
    return kind, config


def _run_action_mutation(
    mutate: Callable[[bool], WatchActionMutationResponse], *, is_edit: bool, dry_run: bool, yes: bool
) -> WatchActionWire | None:
    """Shared dry-run preview -> confirm -> apply for `watch action add`/`edit`,
    mirroring `_run_mutation` for whole-watch (`is_edit` plays the role its
    `watch_id is not None` does). `mutate(dry_run)` calls the api (True for the
    validate-only preview, False to apply). Returns the applied action node, or
    None on `--dry-run` (nothing applied). The interactive `[y/N]` confirm still
    gates a real apply; `--yes` skips it (required when piped). The response
    NESTS the action node under `.action` (dry-run add leaves `action.id` empty
    since nothing persisted)."""
    noun = "edit" if is_edit else "add"
    preview = mutate(True)
    # Server must honor dry_run (mirrors _run_mutation's guard): the preview must
    # be flagged dry_run, and an ADD preview must not carry an id - an id there
    # means a row persisted (an edit preview keeps the existing action's id).
    if not preview.dry_run or (preview.action.id and not is_edit):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a persisted action", preview.action.id, noun="action"
        )
    label = preview.action.id
    title = f"Would {noun} action {label}:" if label else f"Would {noun} this action:"
    _print_action_detail(preview.action, title=title)
    if dry_run:
        console.warn("Dry run only. Nothing was changed.")
        return None
    if not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt for confirmation. Re-run with --yes to {noun}, "
                f"--dry-run to validate only, or run the command without -f to use $EDITOR."
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"{noun.capitalize()} this action?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    result = mutate(False)
    if result.dry_run or not result.action.id:  # the apply must have actually persisted
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.action.id, noun="action")
    return result.action
