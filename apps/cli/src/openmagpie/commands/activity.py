"""`magpie activity ...`: the run audit for one action.

Flat, top-level observability noun (not nested under `watch action`). `summary`
is a per-state breakdown over a window; `list` is the individual run log; `get`
is one run in full. All three scope to an action via `--action`; `get` takes the
run's own id. Works for every action kind; the SCORE column appears only when the
action scores (`semantic_filter`) and is dropped entirely for kinds that don't,
the same way `get`'s reason field is gated to the kinds that produce one.
"""

from __future__ import annotations

from typing import Any

import typer

from openmagpie_schema.watch import (
    WatchActionRunListResponse,
    WatchActionRunView,
    WatchActionRunWire,
    WatchActionWire,
)
from openmagpie_schema.watch_enums import (
    BACKLOG_STATES,
    WatchActionKind,
    WatchActionRunState,
    WatchActivityWindow,
    choices,
)

from .. import console
from ..context import app_ctx
from ._shared import (
    _as_enum,
    _check_choice,
    _Col,
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
)

activity_app = typer.Typer(no_args_is_help=True)

# Human labels for the (server-resolved) windows, keyed by the shared enum so
# there are no magic strings; the CLI picks a value and renders off summary.window.
_WINDOW_LABELS = {
    WatchActivityWindow.DAY: "last 24 hours",
    WatchActivityWindow.YESTERDAY: "yesterday",
    WatchActivityWindow.WEEK: "last 7 days",
    WatchActivityWindow.MONTH: "last 30 days",
}

# Evaluated states in print order, DERIVED from the shared enum (everything but
# the live BACKLOG_STATES) so a new state shows up instead of being dropped.
_EVALUATED_ORDER = [s for s in WatchActionRunState if s not in BACKLOG_STATES]


def _evaluated_label(state: WatchActionRunState) -> str:
    """Row label for the EVALUATED table. FAILED here is the EXHAUSTED kind
    (terminal); the transient/retry-pending FAILED is the separate `retrying`
    backlog row, so spell it out to keep the two counts distinct."""
    return "failed (exhausted)" if state is WatchActionRunState.FAILED else state.value


def _fmt_score(value: object) -> str:
    """A score to 2dp, or '-' for a non-numeric / absent value. Shared by the
    `list` SCORE column (`fmt`) and `get`'s score field so the two never disagree."""
    return f"{value:.2f}" if isinstance(value, (int, float)) else console.EMPTY


def _score(run: WatchActionRunWire) -> str:
    """The semantic-filter score from the result blob, 2dp ; '-' for kinds that
    don't score or runs that never produced one."""
    return _fmt_score(run.result.get("score"))


# ── Per-kind run rendering ──────────────────────────────────────────────────
#
# A run's `result` is a per-kind blob (SemanticFilterResult, WebhookResult,
# LogResult), so the columns / fields worth surfacing are kind-specific. Each
# kind gets a RunFormatter contributing its EXTRA columns (the list view) and
# fields (the detail view) ; the base contributes none, so an unmapped / unknown
# kind still renders the common skeleton. A registry, not an if/elif chain, so a
# new kind is one entry. Mirrors the server's per-kind typed-blob registry.


class RunFormatter:
    """Base: no kind-specific DETAIL fields (the `get` view). The list view is a
    dot-path projection of the run record, so it needs no per-kind columns."""

    def detail_fields(self, run: WatchActionRunWire) -> list[tuple[str, str]]:
        return []


class FilterRunFormatter(RunFormatter):
    """`semantic_filter`: the relevance score + reason on the `get` detail."""

    def detail_fields(self, run: WatchActionRunWire) -> list[tuple[str, str]]:
        fields = [("score", _score(run))]
        reason = run.result.get("reason")
        if reason:
            fields.append(("reason", str(reason)))
        return fields


_RUN_FORMATTERS: dict[WatchActionKind, RunFormatter] = {
    WatchActionKind.SEMANTIC_FILTER: FilterRunFormatter(),
}
_BASE_RUN_FORMATTER = RunFormatter()


def _run_formatter(action: WatchActionWire | None) -> RunFormatter:
    """The formatter for this action's kind, or the base when the action is
    absent (paged response) or its kind is unknown to this CLI build."""
    if action is None:
        return _BASE_RUN_FORMATTER
    kind = _as_enum(action.kind, WatchActionKind)
    if kind is None:
        return _BASE_RUN_FORMATTER
    return _RUN_FORMATTERS.get(kind, _BASE_RUN_FORMATTER)


@activity_app.command("summary")
@_handle_api_errors
def summary(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id to summarize."),
    window: str | None = typer.Option(
        None, "--window", help=f"Window by evaluation time ({choices(WatchActivityWindow)})."
    ),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the summary as one JSON object instead of tables."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """A per-state breakdown of one action's activity over a window."""
    _check_choice(window, WatchActivityWindow)
    win = window or WatchActivityWindow.WEEK.value
    # limit=1: summary mode shows no rows, but the endpoint always returns a page ;
    # ask for the smallest. The server resolves the window and attaches the summary.
    resp = app_ctx().api.activity.list(action_id, window=win, limit=1)
    if jsonl and resp.summary is None:
        # None means "paged response, summary not computed" (not "no activity") ;
        # the first page always carries one, so this is defensive. Never fake a
        # `{}` (the worst option) — signal it, like the human "No summary available."
        console.error("No summary available.")
        raise typer.Exit(code=1)
    _emit_detail(
        render=lambda: _print_summary(action_id, resp),
        # The `else ""` arm is unreachable — the `resp.summary is None` guard
        # above already exited. It is here only because ty can't narrow
        # `resp.summary` into a lambda body ; this is not a live empty path.
        json_obj=lambda: resp.summary.model_dump_json() if resp.summary is not None else "",
        jsonl=jsonl,
        output=output,
    )


@activity_app.command("list")
@_handle_api_errors
def list_(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id whose activity to list."),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter by run state ({choices(WatchActionRunState)})."
    ),
    after: str | None = typer.Option(None, "--after", help="Cursor (activity id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Rows per page."),
    columns: str | None = _columns_option("run.state,feed_item.title,feed_item.url,action.config.threshold"),
    transpose: bool = _transpose_option("run"),
    print_columns: bool = _print_columns_option("run"),
    jsonl: bool = _jsonl_rows_option("run"),
    output: str | None = _list_output_option(paginated=True),
) -> None:
    """The individual runs ("activity entries") for one action, newest first."""
    _check_choice(state, WatchActionRunState)
    _emit_columns_paginated(
        fetch=lambda cursor, lim: app_ctx().api.activity.list(action_id, state=state, after=cursor, limit=lim),
        after=after,
        limit=limit,
        record_of=_run_record,
        default_columns=_run_columns,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No activity matches.",
    )


@activity_app.command("get")
@_handle_api_errors
def get(
    activity_id: str = typer.Argument(..., help="Activity (run) id, from `magpie activity list`."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the run as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """One activity entry in full: the run, the item it judged, its feed, the action."""
    view = app_ctx().api.activity.get(activity_id)
    _emit_detail(render=lambda: _print_activity_detail(view), json_obj=view.model_dump_json, jsonl=jsonl, output=output)


def _print_summary(action_id: str, resp: WatchActionRunListResponse) -> None:
    s = resp.summary
    if s is None:  # defensive ; the first-page call always carries one
        console.log("No summary available.")
        return
    # `.get` fallback: an unmapped (newly-added) window still renders its raw value.
    label = _WINDOW_LABELS.get(s.window, s.window.value)
    # Two pivoted 2-column tables: what the action EVALUATED in the window, then
    # the live backlog (kept apart ; the backlog isn't time-bound).
    pair_cols: list[console.Column[tuple[str, str]]] = [
        console.Column(f"EVALUATED ({label})", lambda kv: kv[0], width=24),
        console.Column("RUNS", lambda kv: kv[1]),
    ]
    console.header(f"activity for action {action_id}")
    console.table([(_evaluated_label(st), str(s.evaluated.get(st, 0))) for st in _EVALUATED_ORDER], pair_cols)
    console.log("")
    backlog_cols: list[console.Column[tuple[str, str]]] = [
        console.Column("BACKLOG (now)", lambda kv: kv[0], width=24),
        console.Column("RUNS", lambda kv: kv[1]),
    ]
    backlog = [("pending", str(s.pending)), ("running", str(s.running)), ("retrying", str(s.retrying))]
    console.table(backlog, backlog_cols)
    # Two distinct verbs, each with its own flags (don't imply `list` takes
    # `--window` or `summary` takes `-s`).
    console.log(f"\nList runs:    magpie activity list --action {action_id} [-s <state>]")
    console.log(f"Other window: magpie activity summary --action {action_id} --window <preset>")


def _run_columns(resp: WatchActionRunListResponse) -> list[_Col]:
    """The `activity list` columns for this page (ACTIVITY ID first, per the
    pk-first rule). SCORE is included only when the action scores, off the TYPED
    `resp.action.kind` (dropped, not a dead `-`, for other kinds); SCHEDULED +
    COMPLETED render to seconds (the pair shows run latency); ERROR is the triage
    column for failed runs (`-` on healthy ones). FEED is not default; reach it
    with `--columns feed.name`, `--transpose`, or `activity get`."""
    # `_as_enum` (not a raw `==`) so an unknown / future kind resolves to None and
    # drops SCORE, matching `_run_formatter`'s idiom.
    scoring = resp.action is not None and _as_enum(resp.action.kind, WatchActionKind) == WatchActionKind.SEMANTIC_FILTER
    return [
        col("ACTIVITY ID:run.id"),
        col("STATE:run.state"),
        *([col("SCORE:run.result.score", fmt=_fmt_score)] if scoring else []),
        col("TITLE:feed_item.title", width=60),
        col("SCHEDULED:run.scheduled_at", fmt=_ts),
        col("COMPLETED:run.completed_at", fmt=_ts),
        col("ERROR:run.error"),
        col("URL:feed_item.url"),
    ]


def _run_record(run: WatchActionRunWire, resp: WatchActionRunListResponse) -> dict[str, Any]:
    """One run as the `{run, feed_item, feed, action}` dict (the CLI-side side-table
    join): the single source for both `--jsonl` and the table. Pruned item / feed /
    removed action -> null. Matches `activity get`'s WatchActionRunView shape."""
    item = resp.feed_items.get(run.feed_item_id)
    feed = resp.feeds.get(item.feed_id) if item is not None else None
    return {
        "run": run.model_dump(mode="json"),
        "feed_item": item.model_dump(mode="json") if item is not None else None,
        "feed": feed.model_dump(mode="json") if feed is not None else None,
        "action": resp.action.model_dump(mode="json") if resp.action is not None else None,
    }


def _print_activity_detail(view: WatchActionRunView) -> None:
    run = view.run
    # state, then the kind-specific fields (e.g. filter score + reason), then the
    # common item / feed / action / timing skeleton.
    fields: list[tuple[str, str]] = [("state", str(run.state)), *_run_formatter(view.action).detail_fields(run)]
    # Honest item fields: each shown only when the connector populated it (no
    # substituting one for another). The item id always identifies the row, even
    # when the item itself was pruned by retention.
    if view.feed_item is not None:
        item = view.feed_item
        if item.title:
            fields.append(("title", item.title))
        if item.url:
            fields.append(("url", item.url))
        if item.source_label:
            fields.append(("source", item.source_label))
    fields.append(("item id", run.feed_item_id))
    if view.feed is not None:
        fields.append(("feed", view.feed.name))
    if view.action is not None:
        fields.append(("action", f"{view.action.kind} (rank {view.action.rank})"))
    fields.append(("scheduled", console.timestamp(run.scheduled_at)))
    fields.append(("completed", console.timestamp(run.completed_at)))
    if run.error:
        fields.append(("error", run.error))
    _print_detail(f"activity {run.id}", fields)
