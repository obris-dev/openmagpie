"""Drain one claimed watch-action run: dispatch, persist, advance.

The per-run unit under `process_due_runs`. The command does the global
work (reap stale, then iterate `claim_due`, which CAS-claims each due run
to RUNNING) ; `WatchDrainOperation` takes one already-claimed run and
carries it to a terminal state, enqueuing the next chain action when it
SUCCEEDS. Mirrors `WatchTriggerOperation` / `FeedPollOperation`: a
one-shot operation with account-scoped services, `.run()` once.

The expensive leg (hydrate + the LLM judge) runs OUTSIDE any transaction.
Only the terminal write + the next-action enqueue are wrapped in one short
atomic block, so a SUCCEEDED run and its successor commit together, no
silently stalled chain, without holding a DB lock across the judge. A
crash before that commit leaves the run RUNNING for the reaper to retry.
"""

import logging
from datetime import datetime
from functools import cached_property

from django.db import transaction
from django.utils import timezone

from feeds.models import FeedItem
from feeds.services import FeedItemService
from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from telemetry import events as telemetry_events
from telemetry.constants import Surface
from watches import run_messages
from watches.actions import registry as actions_registry
from watches.actions.protocol import ActionResult, OutboundActionResult
from watches.models import WatchAction, WatchActionRun
from watches.services import WatchActionDeliveryService, WatchActionRunService, WatchActionService

from .advance import enqueue_next
from .run_inputs import build_run_inputs

logger = logging.getLogger("watches")


class WatchDrainOperation:
    """One-shot: execute one CLAIMED (RUNNING) run to a terminal state."""

    def __init__(self, run: WatchActionRun, *, now: datetime | None = None) -> None:
        # Attribute is `action_run`, not `run`: a `run()` METHOD plus a
        # `self.run` attribute would shadow the method (self.run().run is
        # the row, not callable). `action_run` is also the truer name.
        self.action_run = run
        self.account_id = str(run.account_id)
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
    def delivery_svc(self) -> WatchActionDeliveryService:
        return WatchActionDeliveryService(account_id=self.account_id)

    def run(self) -> ActionResult | None:
        """Dispatch the run and persist the result ; return it, or None if the
        claim was lost (another drain re-claimed after a reap, see `complete`),
        in which case we did NOT write or advance.

        Expected resolution failures become terminal results, not exceptions: a
        deleted action / pruned item / unregistered kind -> ERRORED, an impl
        that raises -> FAILED (retryable). Only an UNEXPECTED error (e.g. the
        commit itself) propagates, for the caller to log and leave to the
        reaper. The terminal write is a guarded CAS; the chain only advances
        (next action enqueued in the SAME txn) when that write WON, so a stale
        completer never double-enqueues."""
        action, result = self._resolve()
        # An outbound call (webhook) is recorded in its OWN commit BEFORE the
        # run CAS (matching the flush), so the audit row is a true record of a
        # real POST even if the completion below loses the claim OR raises. Only
        # the run->delivery LINK is conditional on winning the CAS.
        delivery_id = ""
        if isinstance(result, OutboundActionResult):
            delivery = self.delivery_svc.record(
                watch_id=str(self.action_run.watch_id),
                action_id=str(self.action_run.action_id),
                delivery=DeliveryCadence.INSTANT,
                call=result.outbound,
                outcome_state=result.state,
                error=result.error,
                attempt=self.action_run.attempts,
                now=self.now,
            )
            delivery_id = str(delivery.id)
        with transaction.atomic():
            committed = self.run_svc.complete(
                self.action_run,
                state=result.state,
                result=result.result,
                error=result.error,
                delivery_id=delivery_id,
                now=self.now,
            )
            if committed is None:
                return None  # lost the claim; the fresh winner owns the advance
            if result.state == WatchActionRunState.SUCCEEDED and action is not None:
                # Advance to the next action (instant now, or into a digest
                # window) ; same helper the flush uses, so the path is shared.
                enqueue_next(self.action_run, action, now=self.now)
        # First-ever SUCCEEDED run for this watch -> the activation / TTFV signal.
        # Anonymous telemetry, best-effort; after the commit so the guard query
        # sees this run's terminal state.
        if result.state == WatchActionRunState.SUCCEEDED:
            self._maybe_emit_first_match(action)
        return result

    def _resolve(self) -> tuple[WatchAction | None, ActionResult]:
        """Load the run's action + item, run the kind's impl, and return
        (action, result). The action is returned even on a downstream failure
        so `run` can resolve 'next in chain' ; it is None only when the action
        row itself is gone.

        Every `error` here is a sanitized `run_messages` string (the field
        is operator-facing) ; the raw cause goes to the log keyed by run id."""
        run = self.action_run
        try:
            action = self.action_svc.get(str(run.action_id))
        except WatchAction.DoesNotExist:
            logger.warning("run=%s action=%s no longer exists", run.id, run.action_id)
            return None, ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ACTION_GONE)
        try:
            item = self.feed_item_svc.get(str(run.feed_item_id))
        except FeedItem.DoesNotExist:
            logger.warning("run=%s feed_item=%s no longer exists", run.id, run.feed_item_id)
            return action, ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ITEM_GONE)
        try:
            impl = actions_registry.get(action.kind)
        except KeyError:
            logger.warning("run=%s has no executor for kind=%s", run.id, action.kind)
            return action, ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.NO_EXECUTOR)
        # Uniform dispatch: one item, built like the flush builds a batch, so
        # filter + delivery share the call site. The impl reads what it needs.
        items, context = build_run_inputs(
            [(run, item)],
            watch_id=str(run.watch_id),
            delivery=DeliveryCadence.INSTANT,
        )
        try:
            result = impl.run(action, items=items, context=context)
        except Exception as exc:
            # Protocol contract: an impl raises only on UNEXPECTED failure ->
            # retryable FAILED (the attempt was already burned at claim). Raw
            # cause to the log; the run carries only the sanitized note. (A
            # transient delivery failure is RETURNED as FAILED, not raised, so
            # the failed attempt is still logged ; this catch is the backstop.)
            logger.exception("run=%s kind=%s failed: %s", run.id, action.kind, exc)
            return action, ActionResult(state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT)
        return action, result

    def _maybe_emit_first_match(self, action: WatchAction | None) -> None:
        """Called on EVERY successful run; emits the `first_match` milestone only on
        the watch's first-ever SUCCEEDED run (the activation / TTFV signal). Anonymous
        + best-effort: a telemetry failure never disturbs the drain."""
        with telemetry_events.guard():
            if not telemetry_events.enabled():
                return  # opted out (the default): skip the has_prior_succeeded query
            run = self.action_run
            prior = self.run_svc.has_prior_succeeded(watch_id=str(run.watch_id), exclude_run_id=str(run.pk))
            if prior or action is None:
                return  # not the first match, or (defensively) no action to attribute it to
            telemetry_events.first_match(action_kind=action.kind, surface=Surface.SYSTEM.value)
