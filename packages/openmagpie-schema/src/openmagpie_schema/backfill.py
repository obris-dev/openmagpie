"""Backfill API wire shapes (the `magpie backfill` flow).

Shared, zero-Django source of truth for the backfill endpoints. A backfill
re-runs one action over the feed items the PREVIOUS step passed (or, for a
chain-head target, the watch's feed items in the window), so its output is
regenerated, e.g. after an `extract` action's field set changed.

Two shapes:
  - `BackfillPreview`: the synchronous `--dry-run` answer, how big is this
    before anything is queued (read-only counts, no job written).
  - `BackfillJob`: a queued backfill's state + progress. The POST returns it
    PENDING ; `process_due_backfills` fills the counts and marks it DONE ; a
    `GET /v1/action-backfills/<id>` (backfill status) reads it back.

The window bounds are the ABSOLUTE datetimes the server resolved from the raw
`7d`/ISO request values (against the server clock) and pinned onto the job, so a
relative window doesn't drift between submit and the cron picking it up.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from .watch_enums import WatchActionBackfillState, WatchActionKind


class BackfillPreview(BaseModel):
    """`--dry-run` preview: the size of the backfill without queuing it.

    `dry_run` is always True on a real preview: the server stamps it so the client
    can assert the request was actually honored (a `BackfillJob` response, if the
    server ever ignored `?dry_run`, would validate here with `dry_run` defaulting
    False, so the client catches the silent-side-effect case).

    `source_action_id` is the step whose passes define the set (the target's
    predecessor) ; empty when `source_is_head` (the target is the chain head, so
    the source is the watch's feed items, not an upstream action's runs).

    `matched` is every source pass in the window ; `present`/`pruned` split it by
    whether the feed item still exists (a pruned item can't be re-run).
    `would_delete` is the terminal runs `replace` would remove (target AND every
    downstream action), matching the job's eventual `deleted` (0 without `replace`) ;
    `would_enqueue` is how many target runs the backfill would create (all present
    items with `replace` ; only the ones lacking a target run otherwise).
    """

    dry_run: bool = False
    source_action_id: str = ""
    source_is_head: bool = False
    replace: bool = False
    matched: int = 0
    present: int = 0
    pruned: int = 0
    would_delete: int = 0
    would_enqueue: int = 0


class BackfillJob(BaseModel):
    """A queued backfill's definition + progress.

    The counts are 0 until `process_due_backfills` runs the job, then reflect what
    it actually did: `matched` source passes in the window, `present`/`pruned`
    split, `deleted` terminal runs removed (target + downstream, when `replace`),
    `enqueued` fresh target runs created. `source_action_id` is empty for a
    chain-head target (see `BackfillPreview`).

    `kind` is the target action's kind (a `WatchActionKind`; `| str` tolerates a
    since-removed kind, mirroring the run wire). The four window bounds are the
    resolved absolute datetimes (None = unbounded on that side). `error` carries the
    reason when `state` is FAILED; a FAILED job is retryable while `completed_at` is
    unset and terminal once it's set (the attempts cap was hit), mirroring a run.
    """

    id: str
    state: WatchActionBackfillState
    target_action_id: str
    source_action_id: str = ""
    source_is_head: bool = False
    kind: WatchActionKind | str = ""
    replace: bool = False
    occurred_since: datetime | None = None
    occurred_until: datetime | None = None
    completed_since: datetime | None = None
    completed_until: datetime | None = None
    matched: int = 0
    present: int = 0
    pruned: int = 0
    deleted: int = 0
    enqueued: int = 0
    error: str = ""
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BackfillListResponse(BaseModel):
    """`GET /v1/action-backfills` envelope: this account's backfill jobs, newest-first.
    `next_cursor` is the id to pass as `?after=` for the next page (None at the
    end)."""

    items: list[BackfillJob] = Field(default_factory=list)
    next_cursor: str | None = None
