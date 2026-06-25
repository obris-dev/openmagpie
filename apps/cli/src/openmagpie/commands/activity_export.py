"""`magpie activity export`: export one action's runs (with their feed items) to a
file, for hand-off to another tool. The export sibling of `activity` summary /
list / get (the pattern a future `delivery export` would follow).

A read-only projection over the SAME activity data `magpie activity list` shows,
but shaped for export: it drains the whole matched window (not one page), writes
a file, defaults to CSV, and expands each run's `result` blob into columns -- so
pointing it at an `extract` action yields the hydrated `extracted.*` fields as
columns, and a `semantic_filter` action yields `score` / `reason`. Two independent
time windows filter the rows: `--occurred-*` on the item's source time,
`--completed-*` on when the run finished. Each window flag takes a relative
duration (`7d`, `24h`) or an absolute ISO datetime.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import typer

from openmagpie_schema.watch import WatchActionWire
from openmagpie_schema.watch_actions import (
    EXTRACT_FIELD_NAME_KEY,
    EXTRACT_FIELDS_KEY,
    EXTRACTED_KEY,
    ExtractResult,
    LogResult,
    SemanticFilterResult,
    WebhookResult,
)
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState, choices

from .. import console
from ..context import app_ctx
from ._shared import (
    _DEFAULT_WINDOW,
    _as_enum,
    _build_windows,
    _check_choice,
    _Col,
    _columns_option,
    _emit_columns_items,
    _emit_columns_stream,
    _handle_api_errors,
    _jsonl_rows_option,
    _print_columns_option,
    col,
)
from .activity import _run_record, activity_app

# Rows requested per page: chosen to match the server's pagination cap so the drain
# fetches full pages. NOT an enforced shared constant -- it's just a request size,
# and paging is cursor-driven, so if the server caps lower it returns fewer + a
# cursor and the loop continues (no correctness risk). _MAX_PAGES bounds the drain
# so a runaway feed can't spin forever; if hit, we warn rather than silently truncate.
_PAGE_LIMIT = 200
_MAX_PAGES = 500

# The fixed report columns; the run's `result.*` keys expand in between (see
# _result_columns). COMPLETED is the raw ISO string (not local-formatted like the
# activity table) so the export stays machine-parseable.
_LEADING_COLUMNS = [
    col("TITLE:feed_item.title", width=60),
    col("URL:feed_item.url"),
    col("EXTERNAL_URL:feed_item.external_url"),
    # The item's source time -- the `--occurred-*` filter axis, so a filtered export
    # can be sorted / verified by it. Raw ISO (like COMPLETED), kept machine-parseable.
    col("OCCURRED:feed_item.occurred_at"),
]
_TRAILING_COLUMNS = [col("STATE:run.state"), col("COMPLETED:run.completed_at")]
# Extract's result carries fixed completeness/provenance fields beyond the user's
# declared values; surface them so a CSV can tell a full extraction from an empty
# one (every declared cell blank). DERIVED from the model (minus the user-declared
# `extracted` map) like the other kinds, so a field rename can't silently empty
# them. STATE (always succeeded for extract) / COMPLETED still come from _TRAILING.
_EXTRACT_RESULT_COLUMNS = [col(f"{f.upper()}:run.result.{f}") for f in ExtractResult.model_fields if f != EXTRACTED_KEY]


def _result_columns(records: list[dict]) -> list[_Col]:
    """Expand the union of each run's `result` keys (across all rows) into columns,
    since the dot-path projector has no glob. A scalar value becomes one column; a
    NESTED dict expands one level (`result.<key>.<subkey>`), so any dict-shaped
    result yields a column per sub-field. Generic on purpose -- the page-union path
    serves non-extract kinds, so it special-cases no kind's result shape. Order is
    stable: first-seen across the rows, scalar keys before nested ones."""
    scalar: list[str] = []  # result.<key>
    nested: list[str] = []  # result.<key>.<subkey>, stored as "<key>.<subkey>"
    for record in records:
        result = (record.get("run") or {}).get("result") or {}
        for key, value in result.items():
            if isinstance(value, dict):
                for sub in value:
                    if (path := f"{key}.{sub}") not in nested:
                        nested.append(path)
            elif key not in scalar:
                scalar.append(key)
    return [col(f"{k.upper()}:run.result.{k}") for k in scalar] + [
        col(f"{p.rsplit('.', 1)[-1].upper()}:run.result.{p}") for p in nested
    ]


def _page_union_columns(records: list[dict]) -> list[_Col]:
    """Columns from the union of result keys seen in `records` (used for non-extract
    actions, whose result shape this CLI doesn't know up front)."""
    return [*_LEADING_COLUMNS, *_result_columns(records), *_TRAILING_COLUMNS]


# Each known kind's default columns come from exactly ONE source. USER-DECLARED: the
# columns are named in the action's own config (extract's `config.fields`); today
# extract is the only such kind. FIXED: the result shape is set by the kind, so the
# columns come from its Result model's fields (deterministic, never page-1-dependent).
# Both maps live in the CLI, not the schema -- the schema deliberately keeps the
# kind->class mapping out (the server's watches.registry owns the config side).
_USER_DECLARED_COLUMN_KINDS = {WatchActionKind.EXTRACT}
_RESULT_MODELS = {
    WatchActionKind.SEMANTIC_FILTER: SemanticFilterResult,
    WatchActionKind.LOG: LogResult,
    WatchActionKind.WEBHOOK: WebhookResult,
}
# Completeness (every known kind categorized into exactly one of these two maps) is
# enforced by a test, not an import-time assert -- a kind the CLI doesn't know still
# degrades to the page-union fallback at runtime, so this is a dev-time invariant.


def _user_declared_columns(action: WatchActionWire) -> list[_Col]:
    """Columns the USER declared in the action's config: extract's `config.fields`,
    each becoming its own `result.extracted.<name>`. Only extract declares its output
    columns today (see _USER_DECLARED_COLUMN_KINDS)."""
    declared = action.config.get(EXTRACT_FIELDS_KEY) or []
    names = [str(f[EXTRACT_FIELD_NAME_KEY]) for f in declared if f.get(EXTRACT_FIELD_NAME_KEY)]
    # Header uppercased (COMPANY), matching the fixed + page-union columns (STATE,
    # SCORE); the dot-path keeps the field's literal key.
    return [col(f"{name.upper()}:run.result.{EXTRACTED_KEY}.{name}") for name in names]


def _default_columns_for_action(action: WatchActionWire) -> Callable[[list[dict]], list[_Col]]:
    """A resolver `(page-1 records) -> default columns` for an action (a user
    `--columns` overrides these downstream). Always a resolver, so callers never
    branch on the shape; per kind it reaches to the right SOURCE:

      - a user-declared-columns kind (extract today): the declared fields from its
        config plus extract's fixed result columns (status / enrichment).
      - any other known kind: columns from its Result model's fields (fixed shape).
      - an unknown/future kind: the page-union resolver (reflect the result keys
        seen on the first page).

    The first two are DETERMINISTIC -- their resolver ignores the page, so the CSV
    header is never hostage to which runs land on page 1; only the unknown-kind
    fallback actually inspects it. `action.kind` is normalized through `_as_enum`
    (mirroring the activity formatter lookup), so an unknown value -> None -> the
    page-union fallback. Declared-vs-fixed is a real split (per-instance user data
    vs per-kind code); only this resolver is shared (the export dedupes any header
    clash with the fixed columns)."""
    kind = _as_enum(action.kind, WatchActionKind)
    if kind in _USER_DECLARED_COLUMN_KINDS:
        declared = _user_declared_columns(action)
        if not declared:
            # A corrupt at-rest config redacts to {"error": ...} (no `fields`), so we'd
            # silently export only the fixed columns; flag it rather than mislead.
            console.warn(
                "This extract action declares no readable fields (its config may be unreadable); exporting fixed columns only."
            )
        result_cols = [*declared, *_EXTRACT_RESULT_COLUMNS]
    elif (model := _RESULT_MODELS.get(kind)) is not None:
        result_cols = [col(f"{field.upper()}:run.result.{field}") for field in model.model_fields]
    else:
        return _page_union_columns  # unknown shape: the resolver inspects page 1
    fixed = [*_LEADING_COLUMNS, *result_cols, *_TRAILING_COLUMNS]
    return lambda _page: fixed  # deterministic: same columns regardless of the page


def _iter_records(action_id: str, *, state: str | None, windows: dict[str, str]) -> Iterator[list[dict]]:
    """Yield each page's joined run records ({run, feed_item, feed, action}),
    draining the matched window PAGE BY PAGE so a caller streams without holding
    it all. Bounded by _MAX_PAGES so a runaway never spins; a hit warns (no silent
    cut)."""
    api = app_ctx().api.activity
    cursor: str | None = None
    for _ in range(_MAX_PAGES):
        resp = api.list(action_id, state=state, after=cursor, limit=_PAGE_LIMIT, **windows)
        yield [_run_record(run, resp) for run in resp.items]
        cursor = resp.next_cursor
        if not cursor:
            return
    console.warn(f"Stopped at {_MAX_PAGES * _PAGE_LIMIT} rows; narrow the window to capture the rest.")


@activity_app.command("export")
@_handle_api_errors
def export(
    action_id: str = typer.Option(
        ..., "--action", "-a", help="Action id whose runs to export (e.g. an extract action)."
    ),
    occurred_since: str | None = typer.Option(
        None, "--occurred-since", help="Lower bound on the item's source time (duration like 7d, or an ISO datetime)."
    ),
    occurred_until: str | None = typer.Option(None, "--occurred-until", help="Upper bound on the item's source time."),
    completed_since: str | None = typer.Option(
        None, "--completed-since", help="Lower bound on when the run completed (duration like 7d, or an ISO datetime)."
    ),
    completed_until: str | None = typer.Option(
        None, "--completed-until", help="Upper bound on when the run completed."
    ),
    state: str | None = typer.Option(
        None, "--state", "-s", help=f"Filter by run state ({choices(WatchActionRunState)})."
    ),
    jsonl: bool = _jsonl_rows_option("run"),
    columns: str | None = _columns_option("feed_item.title,feed_item.url,run.result"),
    print_columns: bool = _print_columns_option("run"),
    output: str | None = typer.Option(
        None, "--output", "-o", help="File to write the export to (required, unless --print-columns)."
    ),
) -> None:
    """Export an action's runs (joined to their feed items) to a FILE.

    Writes `-o <file>` (required), as CSV (default) or NDJSON with `--jsonl`. An
    export is a file, not terminal output: browse runs interactively with `magpie
    activity list` instead. Each run's `result` fields become columns, so an
    extract action's hydrated fields export directly. With no window flag it
    defaults to the last 7 days (by completion), not the whole retention window.
    Use `--print-columns` to list the available column dot-paths."""
    # The export needs a destination; check first so `magpie activity export -a X` errors on
    # the missing -o straight away (before the window-default warning or any fetch).
    if not output and not print_columns:
        raise typer.BadParameter("export writes a file: pass -o <path> (or --print-columns to list the columns).")
    _check_choice(state, WatchActionRunState)  # reject a typo'd state client-side (like activity list)
    windows, defaulted = _build_windows(
        occurred_since=occurred_since,
        occurred_until=occurred_until,
        completed_since=completed_since,
        completed_until=completed_until,
    )
    if defaulted:
        console.warn(
            f"No time window given; defaulting to runs completed in the last {_DEFAULT_WINDOW}. "
            "Pass --completed-since / --occurred-since (a duration like 30d or an ISO date) to widen or shift it."
        )

    # --print-columns: sample one page to list the available dot-paths, then exit
    # (a small terminal-friendly listing; no file needed). No get_action / column
    # resolution here -- it lists the sample record's raw paths, not the columns.
    if print_columns:
        first_page = next(_iter_records(action_id, state=state, windows=windows), [])
        _emit_columns_items(
            items=first_page,
            record_of=lambda record: record,
            default_columns=[],  # --print-columns lists the sample record's dot-paths; columns unused
            columns=columns,
            transpose=False,
            print_columns=True,
            jsonl=jsonl,  # forward it so --print-columns --jsonl raises the contradiction (not silently ignored)
            output=output,
            empty_msg="No runs match.",
        )
        return

    # Resolve columns from the action itself (extract -> its declared fields), so
    # they don't hinge on what lands on page 1. Also validates the action exists.
    default_columns = _default_columns_for_action(app_ctx().api.watch.get_action(action_id))

    # The export itself is a file (an open-in-a-spreadsheet artifact, not a 2k-line
    # terminal dump), streamed page-by-page so memory stays bounded. (`-o` presence
    # is guaranteed by the up-front check.)
    written = _emit_columns_stream(
        pages=_iter_records(action_id, state=state, windows=windows),
        record_of=lambda record: record,
        default_columns=default_columns,
        columns=columns,
        jsonl=jsonl,
        output=output,
    )
    console.success(f"Wrote {written} run(s) to {output}")
