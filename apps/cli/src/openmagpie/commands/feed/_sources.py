"""`magpie feed source <verb>`: the feed's source set (what it polls).

A source is operator-authored config and a dependent component of exactly one
feed, so it nests under `feed source` (containment), addressed by its OWN id for
single-row ops (`get` / `delete`) and by `--feed` for collection ops (`list` /
`set` / `export`). `delete` is the single destructive verb (there is no
`remove`): a source belongs to one feed and isn't detachable, so dropping it
from the set IS deleting its row.

Bulk write is `set` (the shape a scrape script naturally emits); `template`
emits a starter file. `add` was dropped: per-kind flag UX (`--kind`/`--url`/
`--meta` ...) scales badly. Create-time is `feed create -f` with an inline
`sources:` block; ongoing mutation is `export -> edit -> set`.
"""

from __future__ import annotations

import json
import sys
from importlib import resources

import typer
import yaml
from pydantic import ValidationError

from openmagpie_schema.feed import SourceInput, SourceSetPayload

from ... import console
from ...api.feed import SourceWire
from ...context import app_ctx
from .._shared import (
    _abort_union_validation_error,
    _check_format,
    _columns_option,
    _emit_columns_items,
    _emit_detail,
    _handle_api_errors,
    _jsonl_rows_option,
    _list_output_option,
    _print_columns_option,
    _print_detail,
    _read_file_or_abort,
    _transpose_option,
    _ts,
    col,
)
from ._apps import source_app

_SET_FILE_SHAPES = (
    'JSON or YAML matching `{"version": "v1", "sources": [{spec, meta, field_map}]}` '
    "(produced by `magpie feed source export` or `magpie feed source template`)."
)

# The `feed source template` starter; a resource so its comments don't bloat here.
_SOURCES_TEMPLATE_YAML = resources.files("openmagpie").joinpath("sources_template.yaml").read_text(encoding="utf-8")


def _parse_set_payload(text: str, source_path: str) -> list[SourceInput]:
    """Parse a `feed source set` file into typed `SourceInput` rows.

    Accepts either the documented `{version, sources: [...]}` envelope
    (`SourceSetPayload`) or a bare list ; operators hand-rolling a JSON
    often skip the wrapper. Catches structural and shape errors
    client-side so the operator gets a clean message before the HTTP
    round-trip."""
    try:
        # JSON first since it's a strict subset of YAML.
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            console.error(f"{source_path}: not valid JSON or YAML ({exc})")
            raise typer.Exit(code=1) from None
    try:
        if isinstance(parsed, dict):
            return SourceSetPayload.model_validate(parsed).sources
        if isinstance(parsed, list):
            return SourceSetPayload.model_validate({"sources": parsed}).sources
    except ValidationError as exc:
        # Shared union-error rendering: strips the source-spec union's internal noise
        # (the `tagged-union[...]` prefix + the plugin fallback's built-in-kind line), so
        # a malformed built-in spec reads as e.g. `sources.0.spec.rss.url: ...`.
        _abort_union_validation_error(exc, header=f"{source_path}: payload doesn't match the expected shape:")
    console.error(f"{source_path}: top-level must be an object or array. {_SET_FILE_SHAPES}")
    raise typer.Exit(code=1)


# ── list (by --feed) ───────────────────────────────────────────────────


# Default `feed source list` columns (HEADER:dot-path). `display` is the wire's
# kind-polymorphic label; spec.kind / spec.subreddit / spec.url are raw paths.
_SOURCE_COLUMNS = [
    col("ID:id"),
    col("SOURCE:display"),
    col("KIND:spec.kind"),
    col("LAST EVENT:last_event_at", fmt=_ts),
    col("META:meta"),
    col("FIELD MAP:field_map"),
]


@source_app.command("list")
@_handle_api_errors
def list_(
    feed_id: str = typer.Option(..., "--feed", help="Feed id whose sources to list."),
    columns: str | None = _columns_option(),
    transpose: bool = _transpose_option("source"),
    print_columns: bool = _print_columns_option("source"),
    jsonl: bool = _jsonl_rows_option("source"),
    output: str | None = _list_output_option(paginated=False),
) -> None:
    """List the sources attached to a feed.

    A read view: rows carry the server-computed `display` label, which `feed source
    set` does NOT accept (SourceInput ignores it). To round-trip a source set, use
    `feed source export` (the set-shaped file), not this `--jsonl`."""
    sources = app_ctx().api.feed.list_sources(feed_id)
    # The count is information the table alone doesn't carry (an empty feed still
    # reports `0 source(s)`); the helper shows it for the human view and keeps it
    # off the machine-output paths.
    _emit_columns_items(
        items=sources,
        record_of=lambda s: s.model_dump(mode="json"),
        default_columns=_SOURCE_COLUMNS,
        columns=columns,
        transpose=transpose,
        print_columns=print_columns,
        jsonl=jsonl,
        output=output,
        empty_msg="No sources on this feed yet.",
        header=f"{len(sources)} source(s)",
    )


# ── get (single, by own id) ────────────────────────────────────────────


@source_app.command("get")
@_handle_api_errors
def get(
    source_id: str = typer.Argument(..., help="Source id (from `magpie feed source list`)."),
    jsonl: bool = typer.Option(False, "--jsonl", help="Emit the source as one JSON object instead of a field table."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Show one source by its own ULID (the feed is resolved server-side)."""
    s = app_ctx().api.feed.get_source(source_id)
    _emit_detail(render=lambda: _print_source_detail(s), json_obj=s.model_dump_json, jsonl=jsonl, output=output)


def _print_source_detail(s: SourceWire) -> None:
    fields: list[tuple[str, str]] = [
        ("source", s.spec.display()),
        ("kind", s.spec.kind),
        ("last event", _ts(s.last_event_at)),
        ("created", _ts(s.created_at)),
        ("meta", str(s.meta) if s.meta else console.EMPTY),
        ("field map", str(s.field_map) if s.field_map else console.EMPTY),
    ]
    _print_detail(f"source {s.id}", fields)


# ── delete (single, by own id) ─────────────────────────────────────────


@source_app.command("delete")
@_handle_api_errors
def delete(
    source_id: str = typer.Argument(..., help="Source id (from `magpie feed source list`)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Delete one source by its own ULID (the feed is resolved server-side).

    Destructive: the source and its polling watermark are removed. SourceWire
    carries no feed name, so the confirm names the source itself ; the server
    still resolves and guards the parent feed."""
    api = app_ctx().api.feed
    s = api.get_source(source_id)  # resolve first so the confirm names what goes
    label = f"{s.spec.display()} ({source_id})"
    if not yes:
        if not sys.stdin.isatty():
            console.warn(f"Piped input: can't prompt. Re-run with --yes to delete source {label}.")
            raise typer.Exit(code=1)
        console.warn(f"Delete source {label}? This drops its polling watermark and cannot be undone.")
        if not typer.confirm("Delete?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)
    api.delete_source(source_id)
    console.success(f"Deleted source {label}")


# ── template (starter file for set) ────────────────────────────────────


def _write_text(text: str, output: str | None, *, what: str) -> None:
    text = text if text.endswith("\n") else text + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    try:
        with open(output, "w") as fh:
            fh.write(text)
    except OSError as exc:
        # Bad path, perms, disk full ; surface a clean message
        # instead of a raw traceback, matching the rest of the CLI.
        console.error(f"failed to write {output}: {exc}")
        raise typer.Exit(code=1) from None
    console.success(f"Wrote {what} to {output}")


@source_app.command("template")
def template(
    format: str = typer.Option(
        "yaml",
        "--format",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter sources file to stdout.

    Pipe to a file (`magpie feed source template > sources.yaml`), edit, then
    run `magpie feed source set --feed <feed_id> -f sources.yaml --dry-run` to
    preview the diff before applying. The yaml template carries inline comments;
    json output is the same structure with comments stripped (for a scripted
    consumer)."""
    fmt = _check_format(format)
    text = _SOURCES_TEMPLATE_YAML if fmt == "yaml" else json.dumps(yaml.safe_load(_SOURCES_TEMPLATE_YAML), indent=2)
    _write_text(text, output, what="template")


# ── set (replace whole list, by --feed) ────────────────────────────────


@source_app.command("set")
@_handle_api_errors
def set_(
    feed_id: str = typer.Option(..., "--feed", help="Feed id whose source list to replace."),
    file: str = typer.Option(
        ...,
        "--file",
        "-f",
        help=(
            "JSON/YAML file with the new sources list "
            "(produced by `magpie feed source template` or `export`); "
            "`-` for stdin."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show the diff but don't apply."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when rows would be removed. Required for piped input."
    ),
) -> None:
    """Replace the feed's full source list (additive + drops missing
    + preserves watermarks on persisted rows). Run `magpie feed source
    template` to see the file shape.

    When the diff would REMOVE rows, the command pauses for an
    interactive `y/N` confirmation (the dropped rows take their
    watermarks with them). Piped input must pre-confirm with
    `--yes`; `--dry-run` always returns without writing."""
    text = sys.stdin.read() if file == "-" else _read_file_or_abort(file)
    items = _parse_set_payload(text, source_path=file)
    body = [item.model_dump(mode="json") for item in items]

    if dry_run:
        result = app_ctx().api.feed.set_sources(feed_id, body, dry_run=True)
        console.success(
            f"would: added={result.added} | removed={result.removed} "
            f"| persisted={result.persisted} | total={result.source_count}"
        )
        return

    # Compute the diff first so the operator (or the confirm gate) sees
    # what's about to disappear before the apply call lands.
    preview = app_ctx().api.feed.set_sources(feed_id, body, dry_run=True)
    if preview.removed > 0 and not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt. {preview.removed} row(s) would be removed; "
                "re-run with --yes to apply, or --dry-run to preview only."
            )
            raise typer.Exit(code=1)
        console.warn(
            f"This will REMOVE {preview.removed} source(s) (and their watermarks): "
            f"added={preview.added} | removed={preview.removed} | persisted={preview.persisted} "
            f"| total={preview.source_count}"
        )
        if not typer.confirm("Apply?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    result = app_ctx().api.feed.set_sources(feed_id, body, dry_run=False)
    console.success(
        f"did: added={result.added} | removed={result.removed} "
        f"| persisted={result.persisted} | total={result.source_count}"
    )


# ── export (by --feed) ─────────────────────────────────────────────────


@source_app.command("export")
@_handle_api_errors
def export(
    feed_id: str = typer.Option(..., "--feed", help="Feed id whose sources to dump."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
    format: str = typer.Option(
        "yaml",
        "--format",
        case_sensitive=False,
        help="Output format: `yaml` (default; matches the template / edit round-trip) or `json`.",
    ),
) -> None:
    """Dump the feed's sources in `set`-compatible form.

    Defaults to YAML so `export -> edit -> set` mirrors `template -> edit -> set`
    (YAML is the on-disk format everywhere else); pass `--format json` for a
    scripted consumer. Includes each source's `last_event_at` so a downstream
    `feed source set` round-trip preserves watermarks (without it the new rows on
    a spec-changed re-import would cold-start to wall-clock now and lose the
    operator's backfill history)."""
    fmt = _check_format(format)
    sources = app_ctx().api.feed.list_sources(feed_id)
    payload = SourceSetPayload(
        sources=[
            SourceInput(
                spec=s.spec,
                meta=s.meta or {},
                field_map=s.field_map or {},
                last_event_at=s.last_event_at,
            )
            for s in sources
        ],
    )
    if fmt == "yaml":
        text = yaml.safe_dump(payload.model_dump(mode="json"), sort_keys=False)
    else:
        text = payload.model_dump_json(indent=2)
    _write_text(text, output, what=f"{len(sources)} source(s)")
