"""Flush one due digest window: drain its batch in cap-sized slices, then close.

The per-window unit under `process_due_digests`. The command does the
global work (iterate due windows) ; `WatchDigestFlushOperation` takes one
window and emits its accumulated batch through the action impl's
`run`, looping cap-sized slices until the window is drained. Mirrors
`WatchDrainOperation`: a one-shot operation with account-scoped services,
`.run()` once.

Single-flight (the command is a SingleFlightCommand), so no concurrent
flush ; the drain never touches digest runs (it excludes digest actions).
So the batch can be gathered without a lock. Only the window close takes
the row lock (select_for_update), to serialize against an arrival opening
a fresh window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from feeds.models import FeedItem
from feeds.services import FeedItemService
from openmagpie_schema.watch_actions import DeliveryConfigBase
from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from watches import run_messages
from watches.actions import registry as actions_registry
from watches.actions.protocol import Action, ActionResult, OutboundActionResult, OutboundCall
from watches.models import WatchAction, WatchActionDigestWindow, WatchActionRun
from watches.registry import load_config
from watches.services import (
    WatchActionDeliveryService,
    WatchActionRunService,
    WatchActionService,
    WatchDigestWindowService,
)

from .advance import enqueue_next_batch
from .result_enforce import enforce_result_schema
from .run_inputs import build_run_inputs

logger = logging.getLogger("watches")


@dataclass(frozen=True)
class _SliceResult:
    """One slice's emit result. `more` drives run()'s drain loop: True means
    the window still has pending runs to emit, False means stop (drained,
    permanently errored, or transiently failed). `count` is how many runs this
    slice committed to a terminal state — what run() accumulates for the
    progress meter."""

    outcome: ActionResult | None
    more: bool
    count: int


class WatchDigestFlushOperation:
    """One-shot: emit one due digest window's accumulated batch."""

    def __init__(self, window: WatchActionDigestWindow, *, now: datetime | None = None) -> None:
        self.window = window
        self.account_id = str(window.account_id)
        self.action_id = str(window.action_id)
        self.now = now or timezone.now()

    @cached_property
    def run_svc(self) -> WatchActionRunService:
        return WatchActionRunService(account_id=self.account_id)

    @cached_property
    def action_svc(self) -> WatchActionService:
        return WatchActionService(account_id=self.account_id)

    @cached_property
    def feed_item_svc(self) -> FeedItemService:
        return FeedItemService(account_id=self.account_id)

    @cached_property
    def digest_svc(self) -> WatchDigestWindowService:
        return WatchDigestWindowService(account_id=self.account_id)

    @cached_property
    def delivery_svc(self) -> WatchActionDeliveryService:
        return WatchActionDeliveryService(account_id=self.account_id)

    def run(self) -> ActionResult | None:
        """Drain one due window: emit its accumulated batch in cap-sized
        slices until the window is empty, then close it. Returns the last
        slice's outcome, or None if there was nothing to emit (closed empty).

        Per slice — on SUCCEEDED: mark the slice succeeded, advance each item's
        chain. On a permanent ERROR (bad config / 4xx / blocked / all items
        gone): mark the slice errored. On a transient failure: leave the runs
        pending + window open and STOP (the next cron flush retries the rest).

        Each slice is CAPPED at DIGEST_MAX_BATCH_ITEMS (bounds peak memory +
        emission size). A window with more pending runs is emitted as
        successive slices WITHIN THIS FLUSH (least-tried then oldest first —
        see digest_batch), looping until close_if_drained reports no pending
        run remains. A window that spans more than one slice logs N/total
        progress each slice, so a long drain doesn't look stuck. (Head-of-line
        cost: a large window delays later windows in the same pass — acceptable
        for now.)

        DELIVERY CONTRACT — at-least-once. The emit is a network call OUTSIDE
        the terminal transaction (a DB lock can't be held across a POST), and
        the batch is gathered by PENDING state with no claim fence (digest
        runs are excluded from claim_due, so there's no RUNNING leg / reaper
        for them). A crash AFTER a successful emit but BEFORE complete_batch
        therefore leaves the runs PENDING -> the next flush re-emits. This is
        a CONSCIOUS choice: the alternative (CAS-claim the batch to RUNNING +
        burn an attempt before emit, recover via the reaper) would entangle
        the digest path with the instant reaper and break the PENDING-only
        gather, for a rare crash window that is already mitigated — webhook
        carries a per-item `key` (source:external_id) for receiver dedup, and
        a duplicate log line is harmless. Receivers MUST dedup on that key.
        (The transient-FAILURE path IS bounded — see fail_batch's attempts
        cap ; only the crash-after-success window is at-least-once.)"""
        last: ActionResult | None = None
        emitted = 0
        total: int | None = None
        while True:
            result = self._flush_slice()
            emitted += result.count
            if result.outcome is not None:
                last = result.outcome
            if not result.more:
                if total is not None:
                    # Logged progress above -> close with a final tally.
                    logger.info("digest action=%s drain complete: %d items", self.action_id, emitted)
                return last
            # Reaching here means the slice filled a whole cap and pending runs
            # remain: a window larger than one slice. Anchor the total once
            # (this slice's items + what's still pending) and log progress, so a
            # long multi-slice drain shows N/total instead of looking stuck.
            if total is None:
                total = emitted + self.run_svc.digest_pending_count(action_id=self.action_id)
            logger.info("digest action=%s drain progress: %d/%d items", self.action_id, emitted, total)

    def _flush_slice(self) -> _SliceResult:
        """Emit one cap-sized slice of the window. `more` is True only when the
        slice committed terminal runs and the window still has pending runs to
        emit (so run() loops again) ; it is False on empty / permanent-error /
        transient-failure, where the caller must stop."""
        runs = self.run_svc.digest_batch(action_id=self.action_id)
        if not runs:
            self._close()
            return _SliceResult(outcome=None, more=False, count=0)

        action, impl, impl_err = self._resolve_action()
        if impl_err is not None:
            self.run_svc.complete_batch(
                [str(r.id) for r in runs], state=WatchActionRunState.ERRORED, error=impl_err, now=self.now
            )
            self._close()
            return _SliceResult(
                outcome=ActionResult(state=WatchActionRunState.ERRORED, error=impl_err), more=False, count=len(runs)
            )
        assert action is not None and impl is not None

        # Build the batch in ONE query (no per-run fetch), dropping runs whose
        # item is gone (mark them ERRORED).
        item_by_id = self.feed_item_svc.get_many(str(r.feed_item_id) for r in runs)
        pairs: list[tuple[WatchActionRun, FeedItem]] = []
        gone_ids: list[str] = []
        for run in runs:
            item = item_by_id.get(str(run.feed_item_id))
            if item is None:
                gone_ids.append(str(run.id))
                continue
            pairs.append((run, item))
        if gone_ids:
            self.run_svc.complete_batch(
                gone_ids, state=WatchActionRunState.ERRORED, error=run_messages.ITEM_GONE, now=self.now
            )
        if not pairs:
            self._close()
            return _SliceResult(
                outcome=ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ITEM_GONE),
                more=False,
                count=len(runs),
            )

        batch_runs = [run for run, _ in pairs]
        # The window is keyed on action_id and an action belongs to exactly one
        # watch, so every run in the batch shares a watch_id. Trust that, but
        # CHECK it with a raise (not an assert ; -O strips asserts, and this
        # gates the watch_id shipped in the wire payload) so a broken invariant
        # fails loud instead of silently shipping a wrong watch_id.
        watch_id = str(batch_runs[0].watch_id)
        if any(str(r.watch_id) != watch_id for r in batch_runs):
            raise RuntimeError(f"digest batch for action={self.action_id} spans multiple watches")
        since, until = self._window_bounds(action)
        items, context = build_run_inputs(
            pairs,
            watch_id=watch_id,
            delivery=DeliveryCadence.DIGEST,
            window_since=since,
            window_until=until,
        )

        # Emit OUTSIDE any transaction (network). A transient failure is
        # RETURNED as FAILED (so the failed attempt can be logged) ; an
        # unexpected bug raises. Both -> _fail_slice: burn an attempt on the
        # batch, leave still-retryable runs pending + window open for the next
        # cron flush, and STOP the loop. Runs that hit the attempts cap go
        # terminal FAILED so a down destination drains the window instead of
        # re-emitting forever. Stopping matters: the failed runs sink behind
        # never-tried runs (gather is least-tried-first), so looping would
        # re-emit a lower-priority slice and hammer a likely-down destination ;
        # the next cron tick retries, with the cron interval as backoff.
        try:
            result = impl.run(action, items=items, context=context)
        except Exception as exc:
            logger.exception("digest flush failed action=%s: %s", self.action_id, exc)
            return self._fail_slice(batch_runs, gone_ids)
        # Enforce the kind's registered result schema (SUCCEEDED-only) here, BEFORE the
        # delivery row + batch complete, so a digest plugin kind carries the same
        # result-shape guarantee as an instant one. Preserves an OutboundActionResult
        # subtype, so a violating delivery still records its audit row below.
        result = enforce_result_schema(str(action.kind), result, label=f"digest action {self.action_id} batch")
        # Record EVERY outbound attempt (success or transient) as a delivery row
        # for the audit. Delivery is at-least-once (a crash before complete_batch
        # re-emits next flush) ; receivers dedup per item on the in-body `key`,
        # so there is no server-side dedup to do here.
        delivery_id = (
            self._record(batch_runs, result.outbound, result) if isinstance(result, OutboundActionResult) else ""
        )
        if result.state == WatchActionRunState.FAILED:
            return self._fail_slice(batch_runs, gone_ids)
        return self._complete_slice(batch_runs, action, result, delivery_id, len(runs))

    def _record(self, batch_runs: list[WatchActionRun], call: OutboundCall, result: ActionResult) -> str:
        """Persist one HTTP attempt as a WatchActionDelivery and return its id.
        attempt = the batch's max prior attempts + 1 (this call's number)."""
        delivery = self.delivery_svc.record(
            watch_id=str(batch_runs[0].watch_id),
            action_id=self.action_id,
            delivery=DeliveryCadence.DIGEST,
            call=call,
            outcome_state=result.state,
            error=result.error,
            attempt=max(r.attempts for r in batch_runs) + 1,
            now=self.now,
        )
        return str(delivery.id)

    def _complete_slice(
        self,
        batch_runs: list[WatchActionRun],
        action: WatchAction,
        result: ActionResult,
        delivery_id: str,
        total: int,
    ) -> _SliceResult:
        """Mark the batch terminal (linked to its delivery), advance the chain
        on SUCCEEDED, and close the window if that drained it. `more` loops the
        caller when pending runs remain (a window larger than one slice).
        `total` is the whole slice (batch + already-completed gone items) for
        the progress meter."""
        batch_ids = [str(r.id) for r in batch_runs]
        with transaction.atomic():
            self.run_svc.complete_batch(
                batch_ids,
                state=result.state,
                result=result.result,
                error=result.error,
                delivery_id=delivery_id,
                # Re-stamp the kind that ACTUALLY ran (the flush dispatches by the
                # action's current kind, which may have changed since enqueue), so
                # run.kind agrees with the written result shape; mirrors drain.py.
                kind=str(action.kind),
                now=self.now,
            )
            if result.state == WatchActionRunState.SUCCEEDED:
                # Successor is the same for the whole batch -> resolve once.
                enqueue_next_batch(batch_runs, action, now=self.now)
            # close_if_drained returns True iff no pending run remains -> drained.
            # If it didn't close, more slices are queued: loop again.
            closed = self.digest_svc.close_if_drained(self.action_id)
        return _SliceResult(outcome=result, more=not closed, count=total)

    def _fail_slice(self, batch_runs: list[WatchActionRun], gone_ids: list[str]) -> _SliceResult:
        """A transient batch failure: burn one attempt per run (terminal at the
        cap), close the window if that drained it, and STOP the loop. Only the
        gone runs went terminal this slice, so they are the drained count ; the
        batch stays pending for the next cron flush."""
        with transaction.atomic():
            self.run_svc.fail_batch([str(r.id) for r in batch_runs], now=self.now)
            self.digest_svc.close_if_drained(self.action_id)
        return _SliceResult(
            outcome=ActionResult(state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT),
            more=False,
            count=len(gone_ids),
        )

    def _window_bounds(self, action: WatchAction) -> tuple[datetime | None, datetime | None]:
        """The window the batch covers: `until` = the window close, `since` =
        close minus the configured interval. `since` is None when the config no
        longer loads (run() ERRORs on it anyway) or has no interval."""
        close_at = self.window.close_at
        if close_at is None:
            return None, None
        try:
            config = load_config(action)
        except ValidationError:
            return None, close_at
        # A windowed action is always a delivery kind (only those carry a
        # digest window) ; read the interval off the typed base, not a duck-typed
        # getattr. A non-delivery config (shouldn't happen here) has no window.
        if not isinstance(config, DeliveryConfigBase) or not config.digest_interval_seconds:
            return None, close_at
        return close_at - timedelta(seconds=config.digest_interval_seconds), close_at

    def _close(self) -> None:
        """Close the window iff drained, in its own short txn (the row lock
        composes with the caller's, but the empty/error paths have no other
        write to share a txn with)."""
        with transaction.atomic():
            self.digest_svc.close_if_drained(self.action_id)

    def _resolve_action(self) -> tuple[WatchAction | None, Action | None, str | None]:
        """The digest action + its impl, or (None, None, sanitized error) when
        the action is gone or has no executor. The impl is returned so the
        caller emits without re-fetching. Only delivery kinds (webhook, log)
        ever carry a digest window, so the impl is always batch-capable here ;
        if a future non-delivery kind somehow had one, its run() would handle
        the batch or raise (caught as a transient)."""
        try:
            action = self.action_svc.get(self.action_id)
        except WatchAction.DoesNotExist:
            logger.warning("digest flush: action=%s no longer exists", self.action_id)
            return None, None, run_messages.ACTION_GONE
        try:
            impl = actions_registry.get(action.kind)
        except KeyError:
            logger.warning("digest flush: action=%s has no executor for kind=%s", self.action_id, action.kind)
            return None, None, run_messages.NO_EXECUTOR
        return action, impl, None
