"""WatchBackfillOperation: the per-job unit under the `process_due_backfills` cron.

Given one CLAIMED (RUNNING) backfill job, it does the heavy setup off the request
path: resolve the source feed items (the predecessor's SUCCEEDED passes, or, for a
chain-head target, the watch's feed items in the window), and when `replace` delete
the target's plus every downstream action's stale terminal runs, then enqueue fresh
PENDING target runs. The existing `process_due_runs` drain executes those and
`enqueue_next` advances the chain, so a replace regenerates downstream fresh without
this operation enqueuing anything beyond the target.

Nothing is materialized in Python: `resolve_present` hands back only definitive
objects for the source set (a count; a `Subquery` the delete/count use as a
`feed_item_id__in` operand, server-side; and a keyset iterator the enqueue streams),
never a malleable QuerySet. No new executor path: this only reads, deletes, and
enqueues rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from functools import cached_property
from typing import NamedTuple

from django.conf import settings
from django.db.models import Subquery
from django.utils import timezone

from feeds.services import FeedItemService
from openmagpie_schema.watch_enums import WatchActionBackfillState
from watches import run_messages
from watches.models import Watch, WatchAction, WatchActionBackfill
from watches.services import WatchActionRunService, WatchActionService, WatchService

_RUNNING = WatchActionBackfillState.RUNNING.value
_DONE = WatchActionBackfillState.DONE.value
_FAILED = WatchActionBackfillState.FAILED.value


class ResolvedPresent(NamedTuple):
    """The backfill's resolved source set as purpose-built objects (no raw QuerySet):
    `subquery` is the `feed_item_id__in` operand for delete + preview counts;
    `id_stream` is the keyset ITERATOR the enqueue consumes (lazy + single-pass, so the
    preview ignoring it is free)."""

    matched: int
    present_count: int
    subquery: Subquery
    id_stream: Iterator[str]


def resolve_present(
    *,
    account_id: str,
    source_action_id: str,
    source_is_head: bool,
    watch_id: str,
    windows: dict[str, datetime],
) -> ResolvedPresent:
    """Resolve the backfill source into a `ResolvedPresent`: the `matched` size, the
    surviving `present_count`, a `subquery` (the surviving feed_item_ids as a
    `feed_item_id__in` operand for delete + preview counts), and an `id_stream` keyset
    iterator (the enqueue stream). Every piece comes from an explicit service method
    over ONE private queryset builder per source, so a count and its subquery/stream
    can't drift, and no caller receives a malleable QuerySet. SHARED by the processor
    and the dry-run preview so both resolve the identical set. Chain-head source -> the
    watch's feed items in the occurred window (all trivially exist); predecessor -> the
    source's SUCCEEDED-run feed items intersected with existence. Raises
    Watch.DoesNotExist for a head source whose watch is gone."""
    item_svc = FeedItemService(account_id=account_id)
    if source_is_head:
        watch_svc = WatchService(account_id=account_id)
        watch = watch_svc.get(watch_id)
        feed_ids = [str(wf.feed_id) for wf in watch_svc.watch_feeds(watch)]
        window = {"feed_ids": feed_ids, "since": windows.get("occurred_since"), "until": windows.get("occurred_until")}
        n = item_svc.count_feed_items_in_occurred_window(**window)
        # every feed item in the window exists, so matched == present
        return ResolvedPresent(
            n,
            n,
            item_svc.feed_items_in_occurred_window_subquery(**window),
            item_svc.iter_feed_items_in_occurred_window(**window),
        )
    run_svc = WatchActionRunService(account_id=account_id)
    candidate_subquery = run_svc.succeeded_feed_item_ids_subquery(source_action_id, **windows)
    return ResolvedPresent(
        run_svc.count_succeeded_feed_items(source_action_id, **windows),
        item_svc.count_existing_feed_item_ids(candidate_subquery),
        item_svc.existing_feed_item_ids_subquery(candidate_subquery),
        item_svc.iter_existing_feed_item_ids(candidate_subquery),
    )


def chain_from(action_svc: WatchActionService, target: WatchAction) -> Iterator[WatchAction]:
    """Yield `target` then each downstream action to the chain tail (via
    next_in_chain). Shared so the preview's would_delete count-walk and the
    processor's delete-walk traverse the chain identically (they already share the
    terminal predicate + the source set; this keeps the traversal from drifting too)."""
    action: WatchAction | None = target
    while action is not None:
        yield action
        action = action_svc.next_in_chain(action)


class BackfillCounts(NamedTuple):
    """What the operation did, mirrored onto the job row + the wire result."""

    matched: int
    present: int
    pruned: int
    deleted: int
    enqueued: int


class WatchBackfillOperation:
    """Run one claimed backfill job to completion (or terminal failure)."""

    def __init__(self, job: WatchActionBackfill, *, now: datetime | None = None) -> None:
        self.job = job
        self.now = now or timezone.now()
        self.account_id = str(job.account_id)

    @cached_property
    def action_svc(self) -> WatchActionService:
        return WatchActionService(account_id=self.account_id)

    @cached_property
    def run_svc(self) -> WatchActionRunService:
        return WatchActionRunService(account_id=self.account_id)

    def run(self) -> BackfillCounts | None:
        """Execute the job. Returns the counts on success (job -> DONE), or None if
        the job failed permanently (job -> FAILED). Raises only on UNEXPECTED errors,
        which the command catches so the job stays RUNNING for the reaper to retry."""
        try:
            target = self.action_svc.get(str(self.job.target_action_id))
        except WatchAction.DoesNotExist:
            self._fail(run_messages.BACKFILL_TARGET_GONE)
            return None
        try:
            present = resolve_present(
                account_id=self.account_id,
                source_action_id=str(self.job.source_action_id),
                source_is_head=self.job.source_is_head,
                watch_id=str(self.job.watch_id),
                windows=self._run_windows(),
            )
        except Watch.DoesNotExist:
            self._fail(run_messages.BACKFILL_WATCH_GONE)
            return None

        # `replace` acts HERE: _delete_phase removes the target's + downstream's stale
        # TERMINAL runs (a no-op when additive). The enqueue below is unconditional and
        # idempotent, so it RE-CREATES a fresh PENDING run for exactly the items left
        # without a target run -- everything under replace (their terminal runs were
        # just deleted), only the never-processed items when additive. Downstream
        # regenerates as the drain advances the chain. So the delete IS the replace.
        deleted = self._delete_phase(target, present.subquery)
        # enqueue_many leaves prior_run_id blank on these runs, by design: a BACKFILL
        # JOB queued them, not an upstream run's enqueue_next advance, which matches
        # prior_run_id's contract (the run that caused this one; there isn't one here).
        enqueued = self.run_svc.enqueue_many(
            watch_id=str(self.job.watch_id),
            action_id=str(target.id),
            kind=str(target.kind),
            feed_item_ids=present.id_stream,
            scheduled_at=self.now,
        )
        counts = BackfillCounts(
            matched=present.matched,
            present=present.present_count,
            pruned=present.matched - present.present_count,
            deleted=deleted,
            enqueued=enqueued,
        )
        self._finish(state=_DONE, counts=counts)
        return counts

    def _run_windows(self) -> dict[str, datetime]:
        """The job's non-null window bounds as the source selectors' kwargs (drop unset)."""
        pairs = {
            "occurred_since": self.job.occurred_since,
            "occurred_until": self.job.occurred_until,
            "completed_since": self.job.completed_since,
            "completed_until": self.job.completed_until,
        }
        return {name: value for name, value in pairs.items() if value is not None}

    def _delete_phase(self, target: WatchAction, present_feed_item_subquery: Subquery) -> int:
        """Delete-once. With `replace`, delete the terminal runs of the target AND
        every downstream action (walking `next_in_chain` to the tail; not a DB
        cascade) whose feed_item_id is in `present_feed_item_subquery` (DB-side): replacing
        an action's output makes downstream output stale, so a replace regenerates the
        whole chain, then stamp
        `replace_deleted_at` BEFORE the caller enqueues. A retried job (marker set)
        skips the delete and returns the stored count.

        Both writes are claim-guarded (CAS on state=RUNNING at our attempts): under a
        multi-host reap+reclaim double-run this can't clobber the winner's row. The
        posture is bounded-redundancy (the lock docs' stance): we don't bail after a
        lost stamp, since the re-enqueue is idempotent, we just don't overwrite."""
        if not self.job.replace:
            return 0
        if self.job.replace_deleted_at is not None:
            return self.job.deleted  # already deleted on a prior attempt; don't re-delete
        deleted = 0
        for action in chain_from(self.action_svc, target):
            deleted += self.run_svc.delete_terminal_for_action(
                str(action.id), watch_id=str(self.job.watch_id), feed_item_subquery=present_feed_item_subquery
            )
        # Stamp the delete-once marker + count BEFORE enqueue (claim-guarded), so a
        # crash after this can't re-run the delete and can't clobber a reclaim's row.
        WatchActionBackfill.objects.filter(id=self.job.id, state=_RUNNING, attempts=self.job.attempts).update(
            replace_deleted_at=self.now, deleted=deleted, updated_at=self.now
        )
        self.job.replace_deleted_at = self.now
        self.job.deleted = deleted
        return deleted

    def _fail(self, message: str) -> None:
        """Mark the job terminally FAILED. Bumps attempts to the cap so `claim_due`
        (which re-picks retryable FAILED under the cap) won't reclaim it: a
        permanent defect must not loop, unlike a reaper-reset transient failure."""
        self._finish(state=_FAILED, error=message, exhaust=True)

    def _finish(
        self, *, state: str, counts: BackfillCounts | None = None, error: str = "", exhaust: bool = False
    ) -> None:
        """Guarded terminal write, mirroring WatchActionRunService.complete's CAS:
        only the worker still holding the claim (RUNNING at our attempts) writes, so a
        reaped+reclaimed double-run can't clobber the winner. `.update()` bypasses
        auto_now, so `updated_at` is set explicitly. On a win the fields are mirrored
        back onto `self.job` so the command's log (`job.error`) is accurate."""
        fields: dict[str, object] = {"state": state, "error": error, "completed_at": self.now, "updated_at": self.now}
        if counts is not None:
            fields |= {
                "matched": counts.matched,
                "present": counts.present,
                "pruned": counts.pruned,
                "deleted": counts.deleted,
                "enqueued": counts.enqueued,
            }
        if exhaust:
            fields["attempts"] = settings.WATCH_BACKFILL_MAX_ATTEMPTS
        won = WatchActionBackfill.objects.filter(id=self.job.id, state=_RUNNING, attempts=self.job.attempts).update(
            **fields
        )
        if won:
            # Mirror the written fields back (like complete()) so a caller reading the
            # job after run(), and the command's failure log, see the real values.
            self.job.state = state
            self.job.error = error
            self.job.completed_at = self.now
            if counts is not None:
                self.job.matched, self.job.present, self.job.pruned = counts.matched, counts.present, counts.pruned
                self.job.deleted, self.job.enqueued = counts.deleted, counts.enqueued
