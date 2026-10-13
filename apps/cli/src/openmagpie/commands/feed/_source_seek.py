"""`magpie feed source seek`: move the polling watermark of one source (by its
own id) or of every source on a feed (`--feed`), in either direction.

Its own module (the rest of the `feed source` verbs are in `_sources.py`) so
each stays under the length cap; imported by the package __init__ for the
verb-registration side effect. The first command that accepts EITHER the
own-id positional OR a scope flag, exactly one: the verb is meaningful on one
row and on the whole set alike.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import typer

from openmagpie_schema.run_windows import WindowKeyword, resolve_window_value

from ... import console
from ...api.feed import FeedApi, SourceSeekResult
from ...context import app_ctx
from .._shared import _abort_unexpected, _handle_api_errors, _ts, window_value_grammar
from ._apps import source_app

_OLDEST = WindowKeyword.OLDEST.value

_SEEK_TO_HELP = (
    f"Target: {window_value_grammar(with_floor=True)}. `{_OLDEST}` is the feed's retention floor. "
    "A future value is rejected, and so is one older than the retention window."
)


def _seek_call(api: FeedApi, *, source_id: str | None, feed_id: str | None, to: str, dry_run: bool) -> SourceSeekResult:
    """Dispatch to the by-own-id or the feed-wide seek, so the preview and the
    apply go through one place. The caller has already enforced exactly-one."""
    if source_id is not None:
        return api.seek_source(source_id, to=to, dry_run=dry_run)
    assert feed_id is not None  # exactly-one is checked by the command
    return api.seek_sources(feed_id, to=to, dry_run=dry_run)


def _seek_line(verb: str, r: SourceSeekResult) -> str:
    """The one tally line every path prints (dry-run `would move`, apply `moved`,
    the confirm + piped refusal). A sentence naming what moves out of the scope
    and the target once (`<verb> K of N source(s) to <ts>`, K = advanced + rewound,
    the `N source(s)` phrasing of the `feed source list` header), then the
    per-direction split; `advanced + rewound + unchanged == N`."""
    return (
        f"{verb} {r.advanced + r.rewound} of {r.source_count} source(s) to {_ts(r.to)}: "
        f"advanced={r.advanced} | rewound={r.rewound} | unchanged={r.unchanged}"
    )


@source_app.command("seek")
@_handle_api_errors
def seek(
    source_id: str | None = typer.Argument(
        None,
        help="Source id to move (from `magpie feed source list`). Omit it and pass --feed to move every source on a feed.",
    ),
    feed_id: str | None = typer.Option(
        None, "--feed", help="Feed id whose EVERY source watermark to move (instead of one source id)."
    ),
    to: str = typer.Option(..., "--to", help=_SEEK_TO_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show the per-direction tally but don't apply."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt. Required for piped input."),
) -> None:
    """Move the polling watermark of one source (by its id) or of every source on a
    feed (`--feed`) to a point in time. `--to` is required (`--to now` for the
    server clock): a seek always says where it is going, never defaults.

    Forward (`advanced` rows): the next poll starts at the target, so unpolled
    items before it are not fetched (a poll already in flight may still record
    them once); the "start fresh from now" case. Backward (`rewound` rows): the
    next poll re-fetches from the target, as far back as the source still serves;
    already-stored items are not duplicated, unseen ones record as new and reach
    your watches (the "re-poll since yesterday" case). The target can't predate
    the feed's retention window: items older than that were pruned and would come
    back as new (and a source rewound right to the floor stays safe only if its
    next poll succeeds before the next prune). Rows already at the target are
    `unchanged`.

    The tally is previewed first; apply pauses for a `y/N` confirmation unless
    `--yes`. The apply sends the same `--to` and the server resolves it again
    when it runs: an ISO instant is exact either way; a relative target (`now`,
    a day keyword, a duration, `oldest`) resolves moments after the preview, by
    however long you took at the prompt, so slightly less rewind; the `moved`
    line shows the instant written. Piped input must pre-confirm with `--yes`;
    `--dry-run` always returns without writing."""
    if (source_id is None) == (feed_id is None):
        # A usage error like a bad --to below: BadParameter so both render the
        # same way and exit 2 (typer's usage-error code), not a bare exit 1.
        raise typer.BadParameter(
            "pass exactly one target: a source id (`magpie feed source seek <source_id>`) "
            "or `--feed <feed_id>` for every source on a feed",
            param_hint="SOURCE_ID / --feed",
        )
    # Fast local validation (format only). Both calls send the value RAW so the
    # server resolves it against ITS clock, same as the activity-export windows.
    # `oldest` is the one keyword whose referent (the feed's retention floor)
    # only the server knows, so here any aware instant stands in for it.
    try:
        # Format check only: any aware instant stands in for the floor here (the
        # server resolves `oldest` against the FEED's floor), so `oldest` passes.
        now = datetime.now(UTC)
        resolve_window_value(to, now=now, floor=now)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--to") from None

    api = app_ctx().api.feed
    # Same contract as `feed source set`: the `would move` line is the --dry-run
    # output; an apply computes the same preview silently (to size the confirm
    # gate) and prints only the `moved` line.
    preview = _seek_call(api, source_id=source_id, feed_id=feed_id, to=to, dry_run=True)
    # The server echoes dry_run so a client can tell a preview from a write. A
    # server that ignored the flag (version skew) has ALREADY moved the rows;
    # fail loudly here rather than prompt for a change that already happened.
    # Mirrors `backfill submit`'s _preview_or_abort.
    if not preview.dry_run:
        # The response carries the tally that was applied: say so, it is what
        # the operator has to undo (a second seek to the previous target).
        console.error(f"The server APPLIED the preview instead: {_seek_line('moved', preview)}")
        raise _abort_unexpected("asked for a dry run but the server did not report one", None, noun="source seek")
    if preview.source_count == 0:
        # Only the --feed form can get here (by-id is one row or a 404). Before
        # the dry-run print on purpose: "would move 0 of 0" helps nobody.
        console.warn("No sources on this feed.")
        return
    if dry_run:
        console.success(_seek_line("would move", preview))
        return
    if preview.advanced == 0 and preview.rewound == 0:
        console.warn("Nothing to do: every source in scope is already at that watermark.")
        return

    if not yes:
        if not sys.stdin.isatty():
            console.warn(
                f"Piped input: can't prompt ({_seek_line('would move', preview)}). "
                "Re-run with --yes to apply, or --dry-run to preview only."
            )
            raise typer.Exit(code=1)
        target = _ts(preview.to)
        notes: list[str] = []
        if preview.advanced:
            notes.append(
                f"{preview.advanced} source(s) move FORWARD: unpolled items up to {target} "
                "are not fetched from the next poll on"
            )
        if preview.rewound:
            notes.append(
                f"{preview.rewound} source(s) move BACK: the next poll re-fetches since {target} "
                "(stored items dedup; unseen ones reach your watches as new)"
            )
        console.warn("; ".join(notes) + f" ({_seek_line('would move', preview)}).")
        if not typer.confirm("Apply?"):
            console.warn("Aborted.")
            raise typer.Exit(code=1)

    # The apply sends the SAME raw value as the preview; the server resolves and
    # checks it again on one clock read inside that request. An ISO target is
    # exact either way. A relative one (`now`, a day keyword, a duration,
    # `oldest`) resolves moments later than the preview: milliseconds under
    # --yes, the prompt's dwell time interactively, always in the safe direction
    # (a later target is less rewind). The `moved` line reports what was written.
    # (Pinning the preview's instant instead froze a target the server's moving
    # retention floor then rejected on apply.) Two races remain, both benign: a
    # poll that WRITES a row between preview and apply is overwritten by the
    # apply's UPDATEs, so the per-row tally can differ (the `moved` line is
    # authoritative); a poll that READ a row before the apply and writes after
    # it loses the compare-and-set in advance_watermark, so it can't undo the seek.
    result = _seek_call(api, source_id=source_id, feed_id=feed_id, to=to, dry_run=False)
    console.success(_seek_line("moved", result))
