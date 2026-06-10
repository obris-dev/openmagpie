"""`magpie watch action ...` verbs: surgical single-action chain edits.

The complement to the whole-watch YAML on `watch create`/`edit`: add or drop one
action without round-tripping the full config. All wrap the server's
`/v1/watches/<id>/actions` (chain list/add) and `/v1/actions/<id>` (per-action
set/remove) endpoints.

The run / delivery AUDIT for an action is not here: it lives under the flat
`magpie activity` and `magpie delivery` nouns (observability is queried, not
walked through its parent chain).
"""

from __future__ import annotations

import sys
from typing import Any

import typer
import yaml

from openmagpie_schema.watch import WatchActionWire

from ... import console
from ...context import app_ctx
from .._shared import _handle_api_errors, _read_file_or_abort
from ._apps import action_app


@action_app.command("list")
@_handle_api_errors
def action_list(watch_id: str = typer.Argument(..., help="Watch id.")) -> None:
    """List a watch's action chain, in rank order."""
    actions = app_ctx().api.watch.list_actions(watch_id)
    columns: list[console.Column[WatchActionWire]] = [
        console.Column("ID", lambda a: a.id),
        console.Column("RANK", lambda a: str(a.rank)),
        console.Column("KIND", lambda a: a.kind),
        console.Column("SUMMARY", lambda a: a.summary.detail or "(no summary)"),
    ]
    if not console.table(actions, columns):
        console.log("No actions yet. Add one with `magpie watch action add`.")


@action_app.command("add")
@_handle_api_errors
def action_add(
    watch_id: str = typer.Argument(..., help="Watch id."),
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


@action_app.command("remove")
@_handle_api_errors
def action_remove(
    action_id: str = typer.Argument(..., help="Action id (from `watch action list`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Remove one action from a watch's chain (the chain renumbers to stay dense)."""
    ac = app_ctx()
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to remove action {action_id}.")
            raise typer.Exit(code=1)
        if not typer.confirm(f"Remove action {action_id}?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    ac.api.watch.remove_action(action_id)
    console.success(f"Removed action {action_id}")


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
