"""`magpie watch action ...` verbs: surgical single-action chain ops.

The complement to the whole-watch YAML on `watch create`/`edit`: inspect, add,
replace, or drop one action without round-tripping the full config. `list`/`add`
take `--watch` (no action id yet); `get`/`set`/`delete` take the action's own id.
All wrap the server's `/v1/watches/<id>/actions` (chain list/add) and
`/v1/actions/<id>` (per-action get/set/delete) endpoints.

The run / delivery AUDIT for an action is not here: it lives under the flat
`magpie activity` and `magpie delivery` nouns (observability is queried, not
walked through its parent chain).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
import yaml

from openmagpie_schema.watch import WatchActionWire

from ... import console
from ...context import app_ctx
from .._shared import _emit_collection, _emit_detail, _handle_api_errors, _print_detail, _read_file_or_abort
from ._apps import action_app


@action_app.command("list")
@_handle_api_errors
def action_list(
    watch_id: str = typer.Option(..., "--watch", "-w", help="Watch id whose action chain to list."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit one JSON object per action (NDJSON) instead of a table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """List a watch's action chain, in rank order."""
    actions = app_ctx().api.watch.list_actions(watch_id)
    _emit_collection(items=actions, render_table=_print_actions, jsonl=jsonl, output=output)


def _print_actions(actions: list[WatchActionWire]) -> None:
    columns: list[console.Column[WatchActionWire]] = [
        console.Column("ID", lambda a: a.id),
        console.Column("RANK", lambda a: str(a.rank)),
        console.Column("KIND", lambda a: a.kind),
        console.Column("SUMMARY", lambda a: a.summary.detail or "(no summary)"),
    ]
    if not console.table(actions, columns):
        console.log("No actions yet. Add one with `magpie watch action add`.")


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


def _print_action_detail(a: WatchActionWire) -> None:
    fields: list[tuple[str, str]] = [
        ("kind", a.kind),
        ("rank", str(a.rank)),
        ("summary", a.summary.detail or "(no summary)"),
    ]
    _print_detail(f"action {a.id}", fields)
    console.log("\nconfig:")  # the server-redacted config blob, in full
    console.log(json.dumps(a.config, indent=2, sort_keys=True))


@action_app.command("add")
@_handle_api_errors
def action_add(
    watch_id: str = typer.Option(..., "--watch", "-w", help="Watch id whose chain to add to."),
    file: str = typer.Option(..., "--file", "-f", help="YAML/JSON action config ('-' for stdin)."),
    rank: int | None = typer.Option(None, "--rank", "-r", help="Insert position (0-based). Appends when omitted."),
) -> None:
    """Add one action to a watch's chain from a config file.

    The file is one action: `{kind: <kind>, config: {...}}` ; the same
    shape as an entry in a watch template's `actions:` list."""
    text = sys.stdin.read() if file == "-" else _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    created = app_ctx().api.watch.add_action(watch_id, kind, config, rank=rank)
    console.success(f"Added {created.kind} at rank {created.rank} ({created.id})")


@action_app.command("set")
@_handle_api_errors
def action_set(
    action_id: str = typer.Argument(..., help="Action id (from `watch action list`)."),
    file: str = typer.Option(..., "--file", "-f", help="YAML/JSON action config ('-' for stdin)."),
) -> None:
    """Replace one action's config in place (same position in the chain).

    The file is one action: `{kind: <kind>, config: {...}}` ; `kind` may
    differ from the current one to swap the node's kind."""
    text = sys.stdin.read() if file == "-" else _read_file_or_abort(file)
    kind, config = _parse_action_or_abort(text)
    updated = app_ctx().api.watch.set_action(action_id, kind, config)
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
        console.error(f"Delete action {label}? The chain renumbers to stay dense.")
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
    return kind, config
