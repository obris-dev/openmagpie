"""`magpie backfill submit` / `status` / `list`: re-run an action over the previous
step's passes.

Flat, top-level noun (NOT nested under `watch action`), like `activity` / `delivery`:
`submit` scopes the target action via `--action`/`-a`, and `status <id>` / `list`
address the JOB by its own id (a job isn't scoped to one watch or action), so it
stands on its own rather than being walked through parents. The submit is async: it
QUEUES a job (the server's `process_due_backfills` cron does the heavy setup, then
`process_due_runs` executes it), so it returns a job id, not a result. Output follows
the shared helpers: `submit` announces via `console.success` (a mutation), `status`
is a detail view (`_emit_detail` -> `--jsonl` / `-o`), `list` is a paginated table
(`_emit_columns_paginated`).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import typer

from openmagpie_schema.backfill import BackfillJob, BackfillPreview

from .. import console
from ..context import app_ctx
from ._shared import (
    _abort_unexpected,
    _columns_option,
    _emit_columns_paginated,
    _emit_detail,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _print_detail,
    _transpose_option,
    _ts,
    col,
    validated_window_params,
)

if TYPE_CHECKING:
    from ..api.watch import WatchApi

backfill_app = typer.Typer(no_args_is_help=True, help="Re-run an action over the previous step's passes.")

# `magpie backfill list` columns, as `HEADER:dot-path` into a job record. REPLACE is
# the mode (destructive reprocess vs additive fill) + DELETED is what it removed; both
# distinguish otherwise-similar rows; other fields via --columns.
_BACKFILL_COLUMNS = [
    col("ID:id"),
    col("STATE:state"),
    col("ACTION:target_action_id"),
    col("REPLACE:replace"),
    col("MATCHED:matched"),
    col("DELETED:deleted"),
    col("ENQUEUED:enqueued"),
    col("CREATED:created_at", fmt=_ts),
]


@backfill_app.command("submit")
@_handle_api_errors
def backfill_submit(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id to re-run (from `magpie watch action list`)."),
    occurred_since: str | None = typer.Option(
        None,
        "--occurred-since",
        help="Lower bound on the item's source time (a duration like 30d, or an ISO datetime).",
    ),
    occurred_until: str | None = typer.Option(None, "--occurred-until", help="Upper bound on the item's source time."),
    completed_since: str | None = typer.Option(
        None, "--completed-since", help="Lower bound on the source run's completion (a duration like 30d, or ISO)."
    ),
    completed_until: str | None = typer.Option(
        None, "--completed-until", help="Upper bound on the source run's completion."
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Also redo items already processed (default: fill only never-processed). Regenerates the whole chain from this action down.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show how many items would be processed, then stop (queue nothing)."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the size preview + confirmation prompt. Required to submit off a TTY (piped), since a submit enqueues real work.",
    ),
) -> None:
    """Queue a re-run of an action over the PREVIOUS step's passes.

    The source is the step before this action (e.g. a semantic_filter feeding an
    extract): every item it passed in the window is run through this action. For a
    chain-head action the source is the watch's feed items in the window (only
    --occurred-* applies there). A time window is REQUIRED.

    By default this is ADDITIVE and non-destructive: it fills only items this action
    never processed. `--replace` also redoes items already done, and because that
    makes downstream output stale, it regenerates the WHOLE chain from this action
    down (deletes this action's and every later action's runs for those items, then
    re-runs). The job is QUEUED: `process_due_backfills` sets it up, then
    `process_due_runs` drains it."""
    # Validate locally for a fast error; the server re-resolves against its clock.
    # Unlike an export, a backfill REQUIRES a window (no default) -- an empty one would
    # scan all of retention.
    raw = validated_window_params(
        occurred_since=occurred_since,
        occurred_until=occurred_until,
        completed_since=completed_since,
        completed_until=completed_until,
    )
    if not raw:
        raise typer.BadParameter(
            "a time window is required: pass --occurred-since / --completed-since (a duration like 30d or an ISO date)."
        )

    api = app_ctx().api.watch
    if dry_run:
        _print_preview(_preview_or_abort(api, action_id, replace=replace, windows=raw))
        return
    # A pipe can't silently mutate (AGENTS.md): any submit off a TTY needs --yes,
    # since even an additive backfill enqueues real (LLM-cost) runs.
    if not yes:
        if not sys.stdin.isatty():
            console.warn("Piped input: can't prompt. Re-run with --yes to submit non-interactively.")
            raise typer.Exit(code=1)
        # Show the size before committing (like `watch action add`'s preview), so the
        # operator sees how many runs this would enqueue, then confirm.
        _print_preview(_preview_or_abort(api, action_id, replace=replace, windows=raw))
        if replace:
            console.warn(
                f"--replace regenerates the whole chain from {action_id} down for the matched items "
                "(deletes this action's and every downstream action's runs, then re-runs them)."
            )
        if not typer.confirm("Queue this backfill?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    job = api.submit_backfill(action_id, replace=replace, windows=raw)
    console.success(f"Queued backfill {job.id} ({job.state}).")
    console.log(f"Check progress:  magpie backfill status {job.id}")


@backfill_app.command("status")
@_handle_api_errors
def backfill_status(
    backfill_id: str = typer.Argument(..., help="Backfill job id (from `magpie backfill submit`)."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the job as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show a backfill job's state + progress counts."""
    job = app_ctx().api.watch.get_backfill(backfill_id)
    _emit_detail(render=lambda: _print_job(job), json_obj=job.model_dump_json, jsonl=jsonl, output=output)


@backfill_app.command("list")
@_handle_api_errors
def backfill_list(
    after: str | None = typer.Option(None, "--after", help="Cursor (backfill id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("backfill"),
    print_columns: bool = _print_columns_option("backfill"),
    jsonl: bool = _jsonl_rows_option("backfill"),
    output: str | None = _list_output_option(paginated=True),
) -> None:
    """List this account's backfill jobs, newest-first."""
    _emit_columns_paginated(
        fetch=lambda cursor, lim: app_ctx().api.watch.list_backfills(after=cursor, limit=lim),
        after=after,
        limit=limit,
        record_of=lambda job, _resp: job.model_dump(mode="json"),
        default_columns=_BACKFILL_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No backfills yet. Queue one with `magpie backfill submit`.",
    )


def _source_label(*, source_is_head: bool, source_action_id: str) -> str:
    return "chain head (the watch's feed items)" if source_is_head else f"the previous step ({source_action_id})"


def _preview_or_abort(api: WatchApi, action_id: str, *, replace: bool, windows: dict[str, str]) -> BackfillPreview:
    """Fetch a `?dry_run=true` size preview, asserting the server honored it. If it
    didn't (old/buggy), it may have queued a REAL (destructive, with --replace)
    backfill, so fail loudly rather than treat the response as a harmless preview.
    Mirrors the _run_mutation guard. Shared by the dry-run branch + the confirm gate."""
    preview = api.preview_backfill(action_id, replace=replace, windows=windows)
    if not preview.dry_run:
        raise _abort_unexpected("asked for a dry run but the server did not report one", None, noun="backfill")
    return preview


def _print_preview(p: BackfillPreview) -> None:
    fields = [
        ("source", _source_label(source_is_head=p.source_is_head, source_action_id=p.source_action_id)),
        ("matched", f"{p.matched} (present {p.present}, pruned {p.pruned})"),
    ]
    if p.replace:
        fields.append(("would delete", f"{p.would_delete} (target + downstream)"))
    fields.append(("would enqueue", str(p.would_enqueue)))
    _print_detail("backfill preview", fields)
    console.log("Dry run only. Nothing was queued.")


def _print_job(j: BackfillJob) -> None:
    fields = [
        ("state", str(j.state)),
        ("action", j.target_action_id),
        ("kind", str(j.kind)),
        ("source", _source_label(source_is_head=j.source_is_head, source_action_id=j.source_action_id)),
        ("replace", str(j.replace)),
    ]
    # Resolved window bounds (only the axis the job carries; `_ts` renders None as `-`).
    if j.occurred_since or j.occurred_until:
        fields.append(("occurred window", f"[{_ts(j.occurred_since)}, {_ts(j.occurred_until)})"))
    if j.completed_since or j.completed_until:
        fields.append(("completed window", f"[{_ts(j.completed_since)}, {_ts(j.completed_until)})"))
    fields += [
        ("matched", str(j.matched)),
        ("present", str(j.present)),
        ("pruned", str(j.pruned)),
        ("deleted", str(j.deleted)),
        ("enqueued", str(j.enqueued)),
        ("created", _ts(j.created_at)),
        ("started", _ts(j.started_at)),
        ("completed", _ts(j.completed_at)),
    ]
    if j.error:
        fields.append(("error", j.error))
    _print_detail(f"backfill {j.id}", fields)
