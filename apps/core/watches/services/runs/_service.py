"""WatchActionRunService: account-scoped run reads + writes (enqueue,
complete, list, summary). The digest-batch surface (digest_batch /
complete_batch / fail_batch) is the DigestBatchMixin."""

from __future__ import annotations

import builtins
import itertools
from collections.abc import Iterable
from datetime import datetime
from typing import NamedTuple

from django.db.models import Count
from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionRunState
from watches.models import WatchActionRun

from .._run_batches import DigestBatchMixin
from ._common import _ENQUEUE_CHUNK, _FAILED, _PENDING, _RUNNING, completion_ts
from ._drain import WatchActionRunGlobal


class ActivitySummary(NamedTuple):
    """`summary_for_action` result. Named (not a bare tuple) so the view reads
    by attribute and a future bucket doesn't shift positional unpacks."""

    evaluated: dict[WatchActionRunState, int]  # terminal states judged in the window
    pending: int
    running: int
    retrying: int


class WatchActionRunService(DigestBatchMixin):
    """Account-scoped run reads + writes (enqueue, complete). The digest-batch
    surface (digest_batch / complete_batch / fail_batch) is the mixin."""

    Global = WatchActionRunGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionRunService requires account_id")
        self.account_id = account_id

    def enqueue(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_id: str,
        scheduled_at: datetime,
        prior_run_id: str = "",
    ) -> WatchActionRun | None:
        """Create a PENDING run for (watch, action, feed_item). Idempotent
        on that triple (unique constraint) ; returns the new run, or None if
        one already exists (so the trigger can re-scan a window safely and a
        completed run is never re-queued). `scheduled_at` is when it's
        relevant (now for instant ; the window close for a digest action).

        Uses get_or_create, not a bare exists()+create: that was TOCTOU (a
        concurrent enqueue of the same triple between the check and the
        insert would raise IntegrityError). The drain calls this INSIDE its
        completion transaction, where a raised IntegrityError would roll the
        whole completion back ; get_or_create wraps the insert in a
        savepoint and absorbs the conflict (re-fetching the winner), so the
        outer transaction survives. Same race-safety enqueue_many gets from
        ignore_conflicts."""
        run, created = WatchActionRun.objects.get_or_create(
            account_id=self.account_id,
            watch_id=watch_id,
            action_id=action_id,
            feed_item_id=feed_item_id,
            defaults={"state": _PENDING, "scheduled_at": scheduled_at, "prior_run_id": prior_run_id},
        )
        return run if created else None

    def enqueue_many(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_ids: Iterable[str],
        scheduled_at: datetime,
    ) -> int:
        """Batch-enqueue PENDING runs of one action over a STREAM of feed
        item ids (the trigger window). Returns the count of NEW runs.

        Consumes the iterable in `_ENQUEUE_CHUNK`-sized chunks so memory
        stays O(chunk) regardless of window size (the caller passes an
        id-only keyset iterator), and `batched` re-chunks ANY iterable, so
        even a caller that hands in a fully-materialized list is still
        processed a chunk at a time. Per chunk: one SELECT of the ids that
        already have a run for this action (idempotent re-scan), one
        bulk_create of the rest with `ignore_conflicts` as a race belt
        against a concurrent trigger ; the unique
        (account, watch, action, feed_item) constraint makes a
        double-insert a silent no-op. The 'new' count comes from the
        pre-filter; under a real race it may over-count by the few rows the
        DB silently dropped, which is fine for a progress total."""
        created = 0
        # strict=False: the final chunk is intentionally short (a window is
        # rarely an exact multiple of the chunk size). strict=True would
        # raise on the remainder.
        for chunk in itertools.batched(feed_item_ids, _ENQUEUE_CHUNK, strict=False):
            created += self._enqueue_chunk(
                watch_id=watch_id, action_id=action_id, feed_item_ids=chunk, scheduled_at=scheduled_at
            )
        return created

    def _enqueue_chunk(
        self,
        *,
        watch_id: str,
        action_id: str,
        feed_item_ids: tuple[str, ...],
        scheduled_at: datetime,
    ) -> int:
        """One chunk: SELECT existing ids, bulk_create the rest. Returns
        the count of new rows.

        Precondition: len(feed_item_ids) <= _ENQUEUE_CHUNK ; the chunk
        feeds an `IN (...)` whose bind-param count must stay under the
        backend ceiling (SQLite caps at 999). `enqueue_many` guarantees
        this via batched() ; the guard catches a direct/out-of-band caller
        before the DB throws a cryptic 'too many SQL variables'. Because
        the chunk is bounded, one bulk_create is one INSERT ; no
        `batch_size` split needed."""
        if len(feed_item_ids) > _ENQUEUE_CHUNK:
            raise ValueError(f"chunk of {len(feed_item_ids)} exceeds _ENQUEUE_CHUNK={_ENQUEUE_CHUNK}")
        have = set(
            WatchActionRun.objects.filter(
                account_id=self.account_id,
                watch_id=watch_id,
                action_id=action_id,
                feed_item_id__in=feed_item_ids,
            ).values_list("feed_item_id", flat=True)
        )
        rows = [
            WatchActionRun(
                account_id=self.account_id,
                watch_id=watch_id,
                action_id=action_id,
                feed_item_id=fid,
                state=_PENDING,
                scheduled_at=scheduled_at,
            )
            for fid in feed_item_ids
            if fid not in have
        ]
        if not rows:
            return 0
        WatchActionRun.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)

    def complete(
        self,
        run: WatchActionRun,
        /,
        *,
        state: WatchActionRunState,
        result: dict | None = None,
        error: str = "",
        delivery_id: str = "",
        now: datetime | None = None,
    ) -> WatchActionRun | None:
        """Write a terminal state + result onto a claimed (RUNNING) run.
        The drain calls this after the action returns ; `state` is the
        outcome (succeeded / gated / errored / skipped) or failed.

        `state` is the enum, not a bare str: the column has no `choices=`,
        so a typo'd / non-terminal value would persist silently and match no
        claim/reap filter (an orphaned row). The service is the enforcement
        point ("no state magic strings") ; `.value` is taken inside.

        Guarded CAS, not a blind save: writes only if the row is STILL
        RUNNING under THIS claim (state == RUNNING AND attempts == the value
        this claim stamped). Returns the run if it won, else None ; None
        means the claim was LOST: the run sat in RUNNING past the stale
        timeout, the reaper flipped it to FAILED, and another drain
        re-claimed (attempts++) and is handling it while this one was still
        judging. The stale completer must then NOT advance the chain (the
        fresh winner does), or it would clobber the authoritative result and
        enqueue the next action a second time (double delivery). The attempts
        match makes the FRESH claim win and the stale one lose deterministically.
        `.update()` bypasses auto_now, so updated_at is set explicitly."""
        if str(run.account_id) != self.account_id:
            raise ValueError(f"run account_id mismatch: {run.account_id!r} not in scope {self.account_id!r}")
        ts = now or timezone.now()
        # completed_at only when this outcome is TERMINAL: a retryable FAILED
        # (transient, under the attempts cap) is NOT done, so it stays null and
        # reads as "retrying", not "evaluated". One rule for every site.
        ct = completion_ts(state.value, run.attempts, ts)
        # delivery_id links the run to the WatchActionDelivery (HTTP call) that
        # carried it ; blank for non-delivery runs (filters, the local log) and
        # left untouched (not cleared) when not supplied.
        fields: dict = {
            "state": state.value,
            "result": result or {},
            "error": error,
            "completed_at": ct,
            "updated_at": ts,
        }
        if delivery_id:
            fields["delivery_id"] = delivery_id
        won = WatchActionRun.objects.filter(id=run.id, state=_RUNNING, attempts=run.attempts).update(**fields)
        if not won:
            return None
        run.state = state.value
        run.result = result or {}
        run.error = error
        run.completed_at = ct
        if delivery_id:
            run.delivery_id = delivery_id
        return run

    def get(self, run_id: str, /) -> WatchActionRun:
        """One run by its id (account-scoped). Raises WatchActionRun.DoesNotExist
        if missing / another account's. The audit detail
        (`/v1/action-activity/<id>`) loads one run to join its item + feed +
        action."""
        return WatchActionRun.objects.get(id=run_id, account_id=self.account_id)

    def list_for_action(
        self,
        action_id: str,
        /,
        *,
        watch_id: str | None = None,
        after: str | None = None,
        limit: int = 50,
        state: str | None = None,
    ) -> builtins.list[WatchActionRun]:
        """This account's runs for one action, newest-first (ULID pk).
        Cursor-paginated for the audit CLI ; `state` filters by run state.
        Pass `watch_id` to scope the query to that watch (the runs table
        denormalizes it), so cross-watch isolation holds in the query, not
        only the caller's guard."""
        qs = WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id)
        if watch_id:
            qs = qs.filter(watch_id=watch_id)
        if state:
            qs = qs.filter(state=state)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])

    def summary_for_action(
        self,
        action_id: str,
        /,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> ActivitySummary:
        """Activity for one action: `(evaluated, pending, running, retrying)`.
        `evaluated` is a per-terminal-state `{state: count}` (enum-keyed) of
        runs JUDGED in [since, until) — windowed on `completed_at` (evaluation
        time, NOT enqueue). The rest are the CURRENT (un-windowed) backlog:
        `pending`/`running` haven't run to a resting state; `retrying` is an
        INSTANT-path transient FAILED still under the attempts cap (FAILED
        with no completed_at), surfaced so a retry-pending run isn't invisible.
        NOTE digest-path transient retries stay PENDING (fail_batch keeps them
        there), so they count under `pending`, not `retrying`. `since`
        required (no all-time scan). GROUP BY + three counts."""
        base = WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id)
        # Index coverage: the evaluated GROUP BY rides `watchrun_activity_idx`
        # (account, action, completed_at); the backlog counts filter on state
        # and ride `watchrun_digest_gather_idx`'s (account, action, state)
        # prefix. If that digest index is ever reshuffled, give the backlog
        # counts their own (account, action, state) cover so they don't
        # silently become account-scans.
        evaluated_qs = base.filter(completed_at__gte=since)
        if until is not None:
            evaluated_qs = evaluated_qs.filter(completed_at__lt=until)
        counts = evaluated_qs.values("state").annotate(n=Count("id"))
        evaluated = {WatchActionRunState(r["state"]): r["n"] for r in counts}
        pending = base.filter(state=_PENDING).count()
        running = base.filter(state=_RUNNING).count()
        # Retry-pending: FAILED but not yet terminal (no completed_at) — the
        # invariant makes this exactly "transient, will be re-claimed".
        retrying = base.filter(state=_FAILED, completed_at__isnull=True).count()
        return ActivitySummary(evaluated=evaluated, pending=pending, running=running, retrying=retrying)
