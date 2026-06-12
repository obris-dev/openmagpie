"""`magpie watch` verbs: template, create, list, get, edit, delete.

A watch subscribes to feeds and runs an ordered action chain over each
new item. YAML is the on-disk format for authoring / replacing the whole
watch (validate -> preview -> confirm -> apply). Single-action edits live
in `_actions.py` (`watch action ...`).
"""

from __future__ import annotations

import sys

import typer
import yaml

from openmagpie_schema.watch import WatchActionInput

from ... import console
from ...api.watch import WatchActionWire, WatchInput, WatchMutationResponse, WatchView
from ...context import AppContext, app_ctx
from .._shared import (
    _abort_unexpected,
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
    col,
)
from ._apps import WATCH_TEMPLATE_YAML, watch_app

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
    seed = yaml.safe_dump(_edit_seed(detail).model_dump(mode="json"), sort_keys=False)
    if file is None:
        body_text = _open_editor_or_abort(seed)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    body = _parse_yaml_or_abort(body_text, WatchInput)
    _run_mutation(ac, body, watch_id=watch_id, dry_run=dry_run, yes=yes)


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


# Default `watch list` columns, as dot-paths into a watch record. ACTIVE maps the
# bool to the same active/paused label `watch get` shows; FEEDS is a list of feed
# ids the renderer joins with `, ` (scalars), not a JSON array. Empty cells render
# `-` on both surfaces (the uniform table convention; `get` aligns to it too).
_WATCH_COLUMNS = [
    col("ID:id"),
    col("NAME:name"),
    col("ACTIVE:is_active", fmt=console.active_or_paused),
    col("FEEDS:feed_ids"),
]


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


def _run_mutation(ac: AppContext, body: WatchInput, *, watch_id: str | None, dry_run: bool, yes: bool) -> None:
    is_edit = watch_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, watch_id=watch_id)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected(
            "asked for a dry run but the server reported a persisted watch", preview.id, noun="watch"
        )
    _print_watch(preview, f"Would {noun} this watch:")

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


def _edit_seed(detail: WatchView) -> WatchInput:
    """The editable envelope for `edit`, projected from the current watch.
    Keeps each action's `id` so the server matches by id (in-place update,
    preserving run history) instead of recreating rows. Drops the
    watch-level read-only fields (user_id, created_at)."""
    return WatchInput(
        name=detail.name,
        is_active=detail.is_active,
        feed_ids=detail.feed_ids,
        actions=[WatchActionInput(id=a.id, kind=a.kind, config=a.config) for a in detail.actions],
    )


def _print_watch(obj: WatchMutationResponse | WatchView, title: str) -> None:
    """Render a watch's config as a pivoted FIELD | VALUE table (the shared
    list renderer, matching feed get), then the action chain as its own
    table. is_active rides in the title, so it's not repeated as a row."""
    console.header(title)
    config_rows: list[tuple[str, str]] = [
        ("name", obj.name),
        ("feeds", ", ".join(obj.feed_ids) or console.EMPTY),
        ("chain", f"{len(obj.actions)} action(s)"),
    ]
    config_columns: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=16),
        # Uncapped: `feeds` is comma-joined feed ids, and there is no other
        # command that lists a watch's feed ids in full, so hiding them behind
        # an ellipsis strands the user. (Unlike feed get's `sources`, which is
        # a deliberate summary backed by `feed source list`.)
        console.Column("VALUE", lambda kv: kv[1], width=0),
    ]
    console.table(config_rows, config_columns)
    if not obj.actions:
        return
    console.log("")  # blank line between the config + chain tables
    chain_columns: list[console.Column[WatchActionWire]] = [
        console.Column("RANK", lambda a: str(a.rank)),
        console.Column("KIND", lambda a: a.kind),
        console.Column("SUMMARY", lambda a: a.summary.detail or console.EMPTY),
    ]
    console.table(obj.actions, chain_columns)
