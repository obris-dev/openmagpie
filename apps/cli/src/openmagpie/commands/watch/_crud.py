"""`magpie watch` verbs: template, create, list, get, edit, delete.

A watch subscribes to feeds and runs an ordered action chain over each
new item. YAML is the on-disk format for authoring / replacing the whole
watch (validate -> preview -> confirm -> apply). Single-action edits live
in `_actions.py` (`watch action ...`).
"""

from __future__ import annotations

import sys
from typing import Any

import typer
import yaml

from openmagpie_schema.watch import WatchActionInput, build_watch_action_input

from ... import console
from ...api.watch import WatchActionWire, WatchInput, WatchMutationResponse, WatchView
from ...context import AppContext, app_ctx
from .._shared import (
    _abort_unexpected,
    _active_flip_note,
    _check_format,
    _columns_option,
    _emit_columns_paginated,
    _emit_detail,
    _emit_doc,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _open_editor_or_abort,
    _parse_yaml_or_abort,
    _print_columns_option,
    _read_file_or_abort,
    _transpose_option,
)
from ._apps import WATCH_TEMPLATE_YAML, watch_app
from ._render import _WATCH_COLUMNS, _print_watch

# ── Template ───────────────────────────────────────────────────────────


@watch_app.command("template")
def template(
    format: str = typer.Option(
        "yaml",
        "--format",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter watch config to stdout."""
    fmt = _check_format(format)
    _emit_doc(WATCH_TEMPLATE_YAML, format=fmt, output=output)


# ── Create ─────────────────────────────────────────────────────────────


@watch_app.command("create")
@_handle_api_errors
def create(
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML config ('-' for stdin). Omit to edit a fresh template in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Create a watch from a YAML config."""
    if file is None:
        body_text = _open_editor_or_abort(WATCH_TEMPLATE_YAML)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    _reject_if_unmodified_template(body_text)
    body = _parse_yaml_or_abort(body_text, WatchInput)
    _run_mutation(app_ctx(), body, watch_id=None, dry_run=dry_run, yes=yes)


# ── Get / Edit / Delete (single watch) ─────────────────────────────────


@watch_app.command("get")
@_handle_api_errors
def get(
    watch_id: str = typer.Argument(..., help="Watch id."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the watch as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one watch's config + action chain."""
    detail = app_ctx().api.watch.get(watch_id)
    _emit_detail(
        render=lambda: _print_watch(detail, f"Watch {detail.id}  [{console.active_or_paused(detail.is_active)}]"),
        json_obj=detail.model_dump_json,
        jsonl=jsonl,
        output=output,
    )


@watch_app.command("edit")
@_handle_api_errors
def edit(
    watch_id: str = typer.Argument(..., help="Watch id."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML to apply ('-' for stdin). Omit to edit the current config in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate the edit and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Full-replace edit of one watch (name, active, feeds, action chain).
    For a single-action tweak, `magpie watch action add/edit/delete` is the
    surgical alternative."""
    ac = app_ctx()
    detail = ac.api.watch.get(watch_id)
    seed = yaml.safe_dump(_edit_seed(detail), sort_keys=False)
    corrupt = _corrupt_config_note(detail.actions)
    if corrupt:
        console.warn(corrupt)
    if file is None:
        body_text = _open_editor_or_abort(seed)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    body = _parse_yaml_or_abort(body_text, WatchInput)
    # A full-replace PUT resumes a paused watch if the body's is_active is true, and an
    # -f file that omits it defaults it true, so warn on the flip (the $EDITOR seed
    # carries the current value, so this only fires for a real change).
    flip = _active_flip_note(current=detail.is_active, submitted=body.is_active, noun="watch", resource_id=watch_id)
    if flip:
        console.warn(flip)
    _run_mutation(ac, body, watch_id=watch_id, dry_run=dry_run, yes=yes, current_actions=detail.actions)


@watch_app.command("delete")
@_handle_api_errors
def delete(
    watch_id: str = typer.Argument(..., help="Watch id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Delete one watch (and its action chain + run history). Not reversible."""
    ac = app_ctx()
    detail = ac.api.watch.get(watch_id)
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete {detail.name} ({detail.id}).")
            raise typer.Exit(code=1)
        console.warn(f"Delete watch {detail.name} ({detail.id})? This cannot be undone.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    ac.api.watch.delete(watch_id)
    console.success(f"Deleted watch {detail.name} ({detail.id})")


# ── List ───────────────────────────────────────────────────────────────


@watch_app.command("list")
@_handle_api_errors
def list_(
    after: str | None = typer.Option(None, "--after", help="Cursor (watch id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("watch"),
    print_columns: bool = _print_columns_option("watch"),
    jsonl: bool = _jsonl_rows_option("watch"),
    output: str | None = _list_output_option(paginated=True),
) -> None:
    """List watches in the caller's account, newest first.

    Cursor-paginated: on a terminal it prompt-pages (Fetch next page? [Y/n]);
    piped/`-o` it emits one page plus the next cursor for a scripted loop."""
    _emit_columns_paginated(
        fetch=lambda cursor, lim: app_ctx().api.watch.list(after=cursor, limit=lim),
        after=after,
        limit=limit,
        record_of=lambda w, _: w.model_dump(mode="json"),
        default_columns=_WATCH_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No watches yet. Try `magpie watch template`.",
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _reject_if_unmodified_template(body_text: str) -> None:
    if body_text.strip() == WATCH_TEMPLATE_YAML.strip():
        console.warn(
            "This is the unmodified template (nothing filled in). Edit it and pass it with "
            "-f, or run `magpie watch create` (no -f) to fill it in interactively."
        )
        raise typer.Exit(code=1)


def _mutate(ac: AppContext, envelope: WatchInput, *, dry_run: bool, watch_id: str | None) -> WatchMutationResponse:
    body = envelope.model_dump(mode="json")
    if watch_id is None:
        return ac.api.watch.create(body, dry_run=dry_run)
    return ac.api.watch.update(watch_id, body, dry_run=dry_run)


def _run_mutation(
    ac: AppContext,
    body: WatchInput,
    *,
    watch_id: str | None,
    dry_run: bool,
    yes: bool,
    current_actions: list[WatchActionWire] | None = None,
) -> None:
    is_edit = watch_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, watch_id=watch_id)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a persisted watch", preview.id, noun="watch"
        )
    _print_watch(preview, f"Would {noun} this watch:")
    if current_actions is not None:  # edit only: flag a by-id chain clobber under the preview
        note = _action_recreate_note(current_actions, body.actions)
        if note:
            console.warn(note)

    if dry_run:
        console.warn("Dry run only. Nothing was changed.")
        return

    if not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt for confirmation. Re-run with --yes to {noun}, "
                f"--dry-run to validate only, or run the command without -f to use $EDITOR."
            )
            raise typer.Exit(code=1)
        if not typer.confirm(f"{noun.capitalize()} this watch?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    result = _mutate(ac, body, dry_run=False, watch_id=watch_id)
    if result.dry_run or not result.id:
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.id, noun="watch")
    done = "Updated" if is_edit else "Created"
    console.success(f"{done} watch {result.name} ({result.id})")


def _edit_seed(detail: WatchView) -> dict[str, Any]:
    """The editable envelope for `edit` (as a JSON-able dict to YAML-dump),
    projected from the current watch. Keeps each action's `id` so the server
    matches by id (in-place update, preserving run history) instead of recreating
    rows. Drops the watch-level read-only fields (user_id, created_at)."""
    return {
        "name": detail.name,
        "is_active": detail.is_active,
        "feed_ids": detail.feed_ids,
        "actions": [_action_edit_seed(a) for a in detail.actions],
    }


def _action_edit_seed(a: WatchActionWire) -> dict[str, Any]:
    """One action's `{id, kind, config, ...}` seed for the edit envelope. A
    corrupt-at-rest config degrades to null on the wire (config=None); seed it as
    an empty `{}` placeholder for the operator to fill rather than crash on the
    None (and rather than feed a None to `build_watch_action_input`, which needs a
    dict). A readable config still round-trips through the typed input envelope."""
    if a.config is None:
        # Match the readable branch's dump so the two seed shapes don't drift:
        # `str(kind)` (a plain string, not the enum) and `rank: None`
        # (build_watch_action_input leaves rank unset -> null).
        return {"id": a.id, "kind": str(a.kind), "rank": None, "config": {}}
    return build_watch_action_input(id=a.id, kind=a.kind, config=a.config.model_dump(mode="json")).model_dump(
        mode="json"
    )


def _corrupt_config_note(actions: list[WatchActionWire]) -> str | None:
    """Warn when a current action's config is unreadable (null on the wire, a
    corrupt-at-rest blob). The edit seed shows it as an empty `{}` placeholder, so
    without this an operator could save an unrelated change and silently overwrite
    the degraded config with defaults (an all-defaults kind like `log` wouldn't
    even raise a validation error, unlike the single-action edit path which flags
    it inline). Lists each by id + kind, mirroring `_action_recreate_note`. None
    when every config is readable."""
    corrupt = [a for a in actions if a.config is None]
    if not corrupt:
        return None
    rows = "\n".join(f"  {a.id}  {a.kind}" for a in corrupt)
    return (
        f"{len(corrupt)} action(s) have an unreadable config, seeded as an empty `{{}}` placeholder:\n"
        f"{rows}\n"
        "Fill each in before saving, or an unrelated edit overwrites the stored config with defaults."
    )


def _action_recreate_note(
    current_actions: list[WatchActionWire], submitted_actions: list[WatchActionInput]
) -> str | None:
    """`watch edit` reconciles the chain BY ID (server `replace_chain`): a
    submitted action without an `id` is a brand-new row, and any current action
    whose id isn't resubmitted is dropped, so its pending activity won't complete.
    Warn when current actions would be dropped this way, listing each by `id` +
    `kind` so the ids are a copy-paste lookup (no `watch action list`
    round-trip). Advisory; the caller still confirms. Returns None when every
    current action's id is carried back."""
    resubmitted = {a.id for a in submitted_actions if a.id}
    dropped = [a for a in current_actions if a.id not in resubmitted]
    if not dropped:
        return None
    rows = "\n".join(f"  {a.id}  {a.kind}" for a in dropped)
    return (
        f"{len(dropped)} existing action(s) will be dropped and their pending activity won't complete:\n"
        f"{rows}\n"
        "If you didn't mean to drop them, add `id: <id>` to the actions you want to keep, "
        "or use `magpie watch action edit <id>`."
    )
