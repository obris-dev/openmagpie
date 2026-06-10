"""`magpie activity ...`: the run audit for one action.

Flat, top-level observability noun (not nested under `watch action`). `summary`
is a per-state breakdown over a window; `list` is the individual run log; `get`
is one run in full. All three scope to an action via `--action`; `get` takes the
run's own id. Works for every action kind — the score / reason columns are shown
only for `semantic_filter` (the only kind that produces them).
"""

from __future__ import annotations

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
from ._shared import _as_enum, _check_choice, _handle_api_errors, _print_detail, _print_next_page

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


def _score(run: WatchActionRunWire) -> str:
    """The semantic-filter score from the result blob, 2dp ; '-' for kinds that
    don't score or runs that never produced one."""
    score = run.result.get("score")
    return f"{score:.2f}" if isinstance(score, (int, float)) else "-"


# ── Per-kind run rendering ──────────────────────────────────────────────────
#
# A run's `result` is a per-kind blob (SemanticFilterResult, WebhookResult,
# LogResult), so the columns / fields worth surfacing are kind-specific. Each
# kind gets a RunFormatter contributing its EXTRA columns (the list view) and
# fields (the detail view) ; the base contributes none, so an unmapped / unknown
# kind still renders the common skeleton. A registry, not an if/elif chain, so a
# new kind is one entry. Mirrors the server's per-kind typed-blob registry.


class RunFormatter:
    """Base: no kind-specific columns or fields. The common skeleton (id, state,
    item, feed, timing) is rendered by the print helpers regardless of kind."""

    def list_columns(self) -> list[console.Column[WatchActionRunWire]]:
        return []

    def detail_fields(self, run: WatchActionRunWire) -> list[tuple[str, str]]:
        return []


class FilterRunFormatter(RunFormatter):
    """`semantic_filter`: the relevance score (list + detail) and reason (detail)."""

    def list_columns(self) -> list[console.Column[WatchActionRunWire]]:
        return [console.Column("SCORE", _score)]

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
) -> None:
    """A per-state breakdown of one action's activity over a window."""
    _check_choice(window, WatchActivityWindow)
    win = window or WatchActivityWindow.WEEK.value
    # limit=1: summary mode shows no rows, but the endpoint always returns a page ;
    # ask for the smallest. The server resolves the window and attaches the summary.
    _print_summary(action_id, app_ctx().api.activity.list(action_id, window=win, limit=1))


@activity_app.command("list")
@_handle_api_errors
def list_(
    action_id: str = typer.Option(..., "--action", "-a", help="Action id whose activity to list."),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter by run state ({choices(WatchActionRunState)})."
    ),
    after: str | None = typer.Option(None, "--after", help="Cursor (activity id) to page after."),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max rows to show."),
) -> None:
    """The individual runs ("activity entries") for one action, newest first."""
    _check_choice(state, WatchActionRunState)
    _print_runs(app_ctx().api.activity.list(action_id, state=state, after=after, limit=limit))


@activity_app.command("get")
@_handle_api_errors
def get(
    activity_id: str = typer.Argument(..., help="Activity (run) id, from `magpie activity list`."),
) -> None:
    """One activity entry in full: the run, the item it judged, its feed, the action."""
    _print_activity_detail(app_ctx().api.activity.get(activity_id))


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


def _print_runs(resp: WatchActionRunListResponse) -> None:
    if not resp.items:
        console.log("No activity matches.")
        return
    # Join each row to its item (title / feed) via the response's keyed side
    # tables, so a reader sees WHAT was judged, not bare ids. A pruned item /
    # feed is simply absent ; fall back to the id.
    items, feeds = resp.feed_items, resp.feeds

    def _title(r: WatchActionRunWire) -> str:
        # Title is title: show it when set, else "-". No substituting url / id
        # for a missing title (an empty title and a pruned item both read "-").
        item = items.get(r.feed_item_id)
        return item.title if (item is not None and item.title) else "-"

    def _feed(r: WatchActionRunWire) -> str:
        item = items.get(r.feed_item_id)
        feed = feeds.get(item.feed_id) if item is not None else None
        return feed.name if feed is not None else "-"

    # Kind-specific columns (e.g. semantic_filter's SCORE) come from the run
    # formatter, slotted between STATE and the common item/feed columns ; the
    # base formatter adds none for log / webhook.
    columns: list[console.Column[WatchActionRunWire]] = [
        console.Column("ACTIVITY ID", lambda r: r.id),
        console.Column("STATE", lambda r: str(r.state)),
        *_run_formatter(resp.action).list_columns(),
        console.Column("TITLE", _title, width=60),
        console.Column("FEED", _feed),
        console.Column("SCHEDULED", lambda r: console.timestamp(r.scheduled_at)),
        console.Column("COMPLETED", lambda r: console.timestamp(r.completed_at)),
        console.Column("ERROR", lambda r: r.error or "-"),
    ]
    console.table(resp.items, columns)
    _print_next_page(resp.next_cursor)


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
