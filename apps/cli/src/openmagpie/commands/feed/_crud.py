"""`magpie feed` verbs: template, create, list, get, edit, delete.

A Feed is the curated set of sources the server polls; its items are the
"sort by new and go" surface (`feed item list`) and what Watches subscribe to.
YAML is the on-disk format. `create` and `edit` share the validate ->
preview -> confirm -> apply flow. The source set lives under `feed source`
(`_sources.py`), the item log under `feed item` (`_items.py`).
"""

from __future__ import annotations

import sys

import typer
import yaml

from ... import console
from ...api.feed import FeedEnvelope, FeedListResponse, FeedMutationResponse, FeedView, FeedWire
from ...context import AppContext, app_ctx
from .._shared import (
    _abort_unexpected,
    _check_format,
    _emit_detail,
    _emit_doc,
    _emit_list,
    _handle_api_errors,
    _open_editor_or_abort,
    _parse_yaml_or_abort,
    _read_file_or_abort,
)
from ._apps import FEED_TEMPLATE_YAML, feed_app

_DEFAULT_LIST_LIMIT = 50


# ── Template ───────────────────────────────────────────────────────────


@feed_app.command("template")
def template(
    format: str = typer.Option(
        "yaml",
        "--format",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter feed config to stdout."""
    fmt = _check_format(format)
    _emit_doc(FEED_TEMPLATE_YAML, format=fmt, output=output)


# ── Create ─────────────────────────────────────────────────────────────


@feed_app.command("create")
@_handle_api_errors
def create(
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML config ('-' for stdin). Omit to edit a fresh template in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate server-side and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Create a feed from a YAML config."""
    if file is None:
        body_text = _open_editor_or_abort(FEED_TEMPLATE_YAML)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    _reject_if_unmodified_template(body_text)
    body = _parse_yaml_or_abort(body_text, FeedEnvelope)
    _run_mutation(app_ctx(), body, feed_id=None, dry_run=dry_run, yes=yes)


# ── Get / Edit / Delete (single feed) ───────────────────────────────────


@feed_app.command("get")
@_handle_api_errors
def get(
    feed_id: str = typer.Argument(..., help="Feed id."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the feed as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one feed's config in the caller's account."""
    detail = app_ctx().api.feed.get(feed_id)
    _emit_detail(
        render=lambda: _print_feed(detail, f"Feed {detail.id}  [{console.active_or_paused(detail.is_active)}]"),
        json_obj=detail.model_dump_json,
        jsonl=jsonl,
        output=output,
    )


@feed_app.command("edit")
@_handle_api_errors
def edit(
    feed_id: str = typer.Argument(..., help="Feed id."),
    file: str | None = typer.Option(
        None, "--file", "-f", help="YAML to apply ('-' for stdin). Omit to edit the current config in $EDITOR."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Validate the edit and show the result, then stop."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Full-replace edit of one feed's config (retention + default_field_map).
    `kind` is server-immutable. Source list mutations go through the
    dedicated verbs (`magpie feed source set` / `delete`); the
    edit YAML deliberately covers only feed-level knobs."""
    ac = app_ctx()
    detail = ac.api.feed.get(feed_id)
    # `sources` is excluded from the dump even though it lives on
    # FeedEnvelope (the create path uses it); the PUT server route
    # silently discards it on edits, so the editor must not show
    # an editable block for it.
    seed = yaml.safe_dump(
        _edit_seed(detail).model_dump(mode="json", exclude={"sources"}),
        sort_keys=False,
    )
    if file is None:
        body_text = _open_editor_or_abort(seed)
    elif file == "-":
        body_text = sys.stdin.read()
    else:
        body_text = _read_file_or_abort(file)
    body = _parse_yaml_or_abort(body_text, FeedEnvelope)
    _run_mutation(ac, body, feed_id=feed_id, dry_run=dry_run, yes=yes)


@feed_app.command("delete")
@_handle_api_errors
def delete(
    feed_id: str = typer.Argument(..., help="Feed id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Delete one feed (and its stored items). Destructive and not reversible."""
    ac = app_ctx()
    detail = ac.api.feed.get(feed_id)
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete {detail.name} ({detail.id}).")
            raise typer.Exit(code=1)
        console.warn(f"Delete feed {detail.name} ({detail.id})? This cannot be undone.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    ac.api.feed.delete(feed_id)
    console.success(f"Deleted feed {detail.name} ({detail.id})")


# ── List ───────────────────────────────────────────────────────────────


@feed_app.command("list")
@_handle_api_errors
def list_(
    after: str | None = typer.Option(None, "--after", help="Cursor (feed id) to page after."),
    limit: int = typer.Option(_DEFAULT_LIST_LIMIT, "--limit", "-l", help="Rows per page."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit one JSON object per feed (NDJSON) instead of a table."),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Write to a file instead of stdout; the next cursor prints to stdout."
    ),
) -> None:
    """List feeds in the caller's account, newest first.

    Cursor-paginated: on a terminal it prompt-pages (Fetch next page? [Y/n]);
    piped/`-o` it emits one page plus the next cursor for a scripted loop."""
    _emit_list(
        fetch=lambda cursor: app_ctx().api.feed.list(after=cursor, limit=limit),
        after=after,
        render_table=_print_feeds,
        jsonl_lines=lambda resp: (f.model_dump_json() for f in resp.items),
        jsonl=jsonl,
        output=output,
    )


def _print_feeds(resp: FeedListResponse) -> None:
    if not resp.items:
        console.log("No feeds yet. Try `magpie feed template`.")
        return
    columns: list[console.Column[FeedWire]] = [
        console.Column("ID", lambda f: f.id),
        console.Column("NAME", lambda f: f.name),
        console.Column("KIND", lambda f: f.kind),
        console.Column("POLL", lambda f: f"{f.poll_interval_seconds}s"),
        console.Column("STATUS", lambda f: console.active_or_paused(f.is_active)),
    ]
    console.table(resp.items, columns)


# ── Helpers ────────────────────────────────────────────────────────────


def _reject_if_unmodified_template(body_text: str) -> None:
    if body_text.strip() == FEED_TEMPLATE_YAML.strip():
        console.warn(
            "This is the unmodified template (nothing filled in). Edit it and pass it with "
            "-f, or run `magpie feed create` (no -f) to fill it in interactively."
        )
        raise typer.Exit(code=1)


def _mutate(ac: AppContext, envelope: FeedEnvelope, *, dry_run: bool, feed_id: str | None) -> FeedMutationResponse:
    body = envelope.model_dump(mode="json")
    if feed_id is None:
        return ac.api.feed.create(body, dry_run=dry_run)
    return ac.api.feed.update(feed_id, body, dry_run=dry_run)


def _run_mutation(ac: AppContext, body: FeedEnvelope, *, feed_id: str | None, dry_run: bool, yes: bool) -> None:
    is_edit = feed_id is not None
    noun = "update" if is_edit else "create"

    preview = _mutate(ac, body, dry_run=True, feed_id=feed_id)
    if not preview.dry_run or (preview.id and not is_edit):
        raise _abort_unexpected("asked for a dry run but the server reported a persisted feed", preview.id, noun="feed")
    _print_feed(preview, f"Would {noun} this feed:")

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
        if not typer.confirm(f"{noun.capitalize()} this feed?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    result = _mutate(ac, body, dry_run=False, feed_id=feed_id)
    if result.dry_run or not result.id:
        raise _abort_unexpected(f"{noun} did not confirm persistence", result.id, noun="feed")
    done = "Updated" if is_edit else "Created"
    console.success(f"{done} feed {result.name} ({result.id})")


def _edit_seed(detail: FeedView) -> FeedEnvelope:
    """The editable envelope for `edit`, projected from the current feed.

    `sources` is a declared field on `FeedEnvelope` (the create-time
    write path uses it), so `extra=ignore` does NOT drop it on a
    naive `model_validate(detail.model_dump())`. The seed YAML
    rendered to $EDITOR would then carry a `sources:` block that the
    server's PUT path silently discards (FeedService.update reads
    only name / poll_interval_seconds / data). Explicit pop is the
    right shape: source list changes go through `feed source set` /
    `delete`, and the operator should never see an editable
    sources block here."""
    body = detail.model_dump()
    # Pop the non-config fields so the seed is editable knobs only: `sources`
    # (see above; edited via `feed source set`/`delete`) and the read-only /
    # server-computed projections source_count / summary / recent_items (the
    # item log, read via `feed item list`).
    for key in ("sources", "source_count", "recent_items", "summary"):
        body.pop(key, None)
    return FeedEnvelope.model_validate(body)


def _sources_value(obj: FeedMutationResponse | FeedView) -> str:
    """The `sources` cell: `(count) name, name, ...`. The table renderer
    truncates the long list to the column cap (a 1093-source feed shows the
    count + a peek, not the whole roster) ; the full list is `feed source list`.
    `(count)` alone when rows aren't echoed (e.g. the create dry-run, which
    reports the would-be count without materializing Source rows)."""
    if obj.source_count == 0:
        return "(0) (none)"
    # SourceWire.spec is the typed SourceSpec union; use `.display()`
    # (every variant implements it) ; `.get(...)` would AttributeError.
    display = ", ".join(s.spec.display() for s in obj.sources)
    return f"({obj.source_count}) {display}" if display else f"({obj.source_count})"


def _print_feed(obj: FeedMutationResponse | FeedView, title: str) -> None:
    """Render a feed's config as a pivoted FIELD | VALUE table (the shared
    list renderer), so it reads like every other view and one long cell
    (sources) truncates instead of blowing out the line."""
    console.header(title)
    rows: list[tuple[str, str]] = [
        ("name", obj.name),
        ("kind", obj.kind),
        ("poll interval", f"{obj.poll_interval_seconds}s"),
        ("sources", _sources_value(obj)),
    ]
    columns: list[console.Column[tuple[str, str]]] = [
        console.Column("FIELD", lambda kv: kv[0], width=16),
        console.Column("VALUE", lambda kv: kv[1], width=64),
    ]
    console.table(rows, columns)
