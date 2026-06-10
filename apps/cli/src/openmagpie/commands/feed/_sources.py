"""`magpie feed <source-verb>` commands: list / remove / set / export / template-sources.

Companion to `_crud.py`; adds the source verbs to the same `feed_app`
Typer so `magpie feed --help` shows everything in one place.

Bulk write is `set-sources` (the shape an external scrape script
naturally produces); `template-sources` emits a starter file so a
new user sees what shape set-sources expects without reading code.
`remove-source <source_id>` is the one single-row mutation that survives ;
it identifies the source by its own id (the server resolves the feed), no
bespoke per-kind flags.

`add-source` was considered and dropped: the flag UX (`--kind`,
`--url`, `--meta key=value` ...) is bespoke per source kind and
scales badly past one or two rows. The create-time path is
`feed create -f feed.yaml` with an inline `sources:` block; ongoing
mutation is `export-sources -> edit -> set-sources`.
"""

from __future__ import annotations

import json
import sys

import typer
import yaml
from pydantic import ValidationError

from openmagpie_schema.feed import SourceInput, SourceSetPayload

from ... import console
from ...api.feed import SourceWire
from ...context import app_ctx
from .._shared import _check_format, _handle_api_errors, _read_file_or_abort
from ._apps import feed_app

_SET_FILE_SHAPES = (
    'JSON or YAML matching `{"version": "v1", "sources": [{spec, meta, field_map}]}` '
    "(produced by `magpie feed export-sources` or `magpie feed template-sources`)."
)

_SOURCES_TEMPLATE_YAML = """\
# Source list for `magpie feed set-sources <feed_id> -f file.yaml`.
#
# `set-sources` REPLACES the feed's full source list: new rows are
# added, missing ones are dropped, and rows that survive the diff
# keep their per-source watermarks. Always run `set-sources` with
# `--dry-run` first to preview the {added, removed, persisted} counts
# before applying.
#
# `magpie feed export-sources <feed_id>` emits exactly this shape, so
# the round-trip is `export -> edit -> set-sources` (or `template-sources
# -> fill in -> set-sources` if you're starting from scratch).

version: v1
sources:
  # RSS source: any feed URL feedparser can parse.
  - spec:
      kind: rss
      url: https://example.com/feed.rss
      name: Example                       # optional display label

    # `meta` (optional): operator-supplied tags. Each entry is copied
    # onto every FeedItem this source produces, so a downstream UI
    # can filter ("show me items tagged `research`"). Free-form
    # key/value; the server doesn't interpret it.
    meta:
      tag: research

    # `field_map` (optional): per-source connector hints. Maps a
    # canonical SourcePayload field (`content`, `external_id`, ...)
    # to where the connector should read it from the upstream
    # payload. Useful when a publisher puts the body in `summary`
    # instead of `content`, or when an auto-generated `external_id`
    # rotates and you want to dedupe by `link` instead. Per-key
    # override of the feed's `default_field_map`; empty = inherit.
    field_map:
      content: summary

    # `last_event_at` (optional): pin the starting watermark to a
    # past datetime to backfill from that point. Omit to default to
    # wall-clock now (live mode from this moment).
    # last_event_at: "2026-05-28T00:00:00Z"

  # Reddit subreddit source: the `/new` feed of one subreddit.
  - spec:
      kind: reddit_subreddit
      subreddit: ClaudeAI
"""


def _parse_set_payload(text: str, source_path: str) -> list[SourceInput]:
    """Parse a set-sources file into typed `SourceInput` rows.

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
        console.error(f"{source_path}: payload doesn't match the expected shape:")
        for err in exc.errors():
            path = ".".join(str(p) for p in err["loc"]) or "_"
            console.error(f"  {path}: {err['msg']}")
        raise typer.Exit(code=1) from None
    console.error(f"{source_path}: top-level must be an object or array. {_SET_FILE_SHAPES}")
    raise typer.Exit(code=1)


# ── list ───────────────────────────────────────────────────────────────


# SourceWire.spec is the typed SourceSpec union (a Pydantic model instance),
# not a dict ; `.display()` + `.kind` are on every variant.
_SOURCE_COLUMNS: list[console.Column[SourceWire]] = [
    console.Column("ID", lambda s: s.id),
    console.Column("SOURCE", lambda s: s.spec.display()),
    console.Column("KIND", lambda s: s.spec.kind),
    console.Column("LAST EVENT", lambda s: str(s.last_event_at)),
    console.Column("META", lambda s: str(s.meta) if s.meta else "-"),
    console.Column("FIELD MAP", lambda s: str(s.field_map) if s.field_map else "-"),
]


@feed_app.command("list-sources")
@_handle_api_errors
def list_sources(feed_id: str = typer.Argument(..., help="Feed id.")) -> None:
    """List the sources attached to a feed."""
    sources = app_ctx().api.feed.list_sources(feed_id)
    console.header(f"{len(sources)} source(s)")
    if not console.table(sources, _SOURCE_COLUMNS):
        console.log("No sources on this feed yet.")


# ── remove (single, by id) ─────────────────────────────────────────────


@feed_app.command("remove-source")
@_handle_api_errors
def remove_source(
    source_id: str = typer.Argument(..., help="Source id (copy from `list-sources`)."),
) -> None:
    """Remove one source by its own ULID (the feed is resolved server-side)."""
    app_ctx().api.feed.remove_source(source_id)
    console.success(f"Removed source {source_id}")


# ── template (starter file for set-sources) ────────────────────────────


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


@feed_app.command("template-sources")
def template_sources(
    format: str = typer.Option(
        "yaml",
        "--format",
        "-F",
        case_sensitive=False,
        help="Output format: `yaml` (commented; default) or `json` (structural; no comments).",
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
) -> None:
    """Emit a starter sources file to stdout.

    Pipe to a file (`magpie feed template-sources > sources.yaml`),
    edit, then run `magpie feed set-sources <feed_id> -f sources.yaml
    --dry-run` to preview the diff before applying. The yaml template
    carries inline comments; json output is the same structure with
    comments stripped (for a scripted consumer)."""
    fmt = _check_format(format)
    text = _SOURCES_TEMPLATE_YAML if fmt == "yaml" else json.dumps(yaml.safe_load(_SOURCES_TEMPLATE_YAML), indent=2)
    _write_text(text, output, what="template")


# ── set (replace whole list) ───────────────────────────────────────────


@feed_app.command("set-sources")
@_handle_api_errors
def set_sources(
    feed_id: str = typer.Argument(..., help="Feed id."),
    file: str = typer.Option(
        ...,
        "--file",
        "-f",
        help=(
            "JSON/YAML file with the new sources list "
            "(produced by `magpie feed template-sources` or `export-sources`); "
            "`-` for stdin."
        ),
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show the diff but don't apply."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt when rows would be removed. Required for piped input."
    ),
) -> None:
    """Replace the feed's full source list (additive + drops missing
    + preserves watermarks on persisted rows). Run `magpie feed
    template-sources` to see the file shape.

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
        console.error(
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


# ── export ─────────────────────────────────────────────────────────────


@feed_app.command("export-sources")
@_handle_api_errors
def export_sources(
    feed_id: str = typer.Argument(..., help="Feed id."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write to a file instead of stdout."),
    format: str = typer.Option(
        "json",
        "--format",
        "-F",
        case_sensitive=False,
        help="Output format: `json` (default; what scrape scripts naturally emit) or `yaml` (human-editable).",
    ),
) -> None:
    """Dump the feed's sources in `set-sources`-compatible form.

    Includes each source's `last_event_at` so a downstream
    `set-sources` round-trip preserves watermarks (without it the
    new rows on a spec-changed re-import would cold-start to
    wall-clock now and lose the operator's backfill history)."""
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
