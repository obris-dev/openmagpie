"""Batch run operations, mixed into `WatchActionRunService`.

A digest delivery emits a whole window of items as ONE batch (see
`watches.operations.digest_flush`). These methods gather and resolve that
batch, and batch-enqueue a chain advance ; they live apart from the
per-item enqueue/claim/complete surface in `runs.py` to keep each module
focused on one concern (and under the file-length cap). The mixin expects
`self.account_id` from the service.
"""

from __future__ import annotations

import builtins
import itertools
from collections.abc import Iterable
from datetime import datetime

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from common.db import ID_IN_CHUNK
from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages
from watches.models import WatchActionRun

_PENDING = WatchActionRunState.PENDING.value
_FAILED = WatchActionRunState.FAILED.value


class DigestBatchMixin:
    """Batch run-queue surface (gather / complete / fail a batch ; batch-
    enqueue a chain advance)."""

    account_id: str

    def enqueue_advance_batch(
        self,
        *,
        action_id: str,
        kind: str,
        scheduled_at: datetime,
        rows: Iterable[tuple[str, str, str]],
    ) -> None:
        """Batch-enqueue PENDING runs for ONE successor `action_id`, one row
        per `(watch_id, feed_item_id, prior_run_id)`. `kind` is the successor
        action's kind, denormalized onto each run so its typed result stays
        renderable even if the action is later deleted. The chain advance (drain +
        digest flush) uses this to fan a SUCCEEDED batch into its successor in
        a single INSERT instead of an N+1 of `enqueue()` round trips.

        Idempotent like `enqueue()`: `bulk_create(ignore_conflicts=True)` emits
        INSERT ... ON CONFLICT DO NOTHING, so the unique
        (account, watch, action, feed_item) constraint silently drops a re-
        enqueue / concurrent insert WITHOUT raising — the same race-safety
        `enqueue`'s get_or_create gives, which matters because the caller runs
        this INSIDE its completion txn (a raised IntegrityError would roll it
        back). No `created` count is returned (the advance caller doesn't use
        one) ; bulk_create's default batch size splits the INSERT under the
        backend's bind-param ceiling. Distinct from `enqueue_many` (trigger
        fan-IN: one action over many feed items, no prior-run link)."""
        objs = [
            WatchActionRun(
                account_id=self.account_id,
                watch_id=watch_id,
                action_id=action_id,
                kind=kind,
                feed_item_id=feed_item_id,
                state=_PENDING,
                scheduled_at=scheduled_at,
                prior_run_id=prior_run_id,
            )
            for watch_id, feed_item_id, prior_run_id in rows
        ]
        if objs:
            WatchActionRun.objects.bulk_create(objs, ignore_conflicts=True)

    def digest_batch(self, *, action_id: str) -> builtins.list[WatchActionRun]:
        """The PENDING runs of a digest action — the accumulated batch the
        flush emits. A digest action's runs are never drained, so its only
        pending runs are the un-emitted batch.

        Ordered LEAST-TRIED-then-oldest (attempts asc, ULID asc): never-tried
        runs (attempts=0) gather ahead of ones that have already failed a
        transient flush. This stops a persistently-failing slice from
        head-of-line-blocking fresh items — as a poison slice's attempts climb
        it sinks behind newer arrivals, so the window keeps delivering new
        content instead of stalling on the stuck batch. The tradeoff: a stuck
        old item can land in a LATER digest than newer items (delivery is no
        longer strictly chronological), which for a digest beats starvation.

        CAPPED at DIGEST_MAX_BATCH_ITEMS: the flush loads every returned item's
        data to build one payload, so an uncapped window would be unbounded in
        memory + emission size + id__in width. A larger window drains over
        successive slices (least-tried first ; the window stays open until
        empty).

        No claim fence (no PENDING->RUNNING): digest delivery is at-least-once
        by design — a crash after a successful emit but before complete_batch
        re-gathers here and re-emits. Receivers dedup on the per-item key.
        See WatchDigestFlushOperation.run's DELIVERY CONTRACT note."""
        return builtins.list(
            WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id, state=_PENDING).order_by(
                "attempts", "id"
            )[: settings.DIGEST_MAX_BATCH_ITEMS]
        )

    def digest_pending_count(self, *, action_id: str) -> int:
        """Total PENDING runs for a digest action — the un-emitted batch size.
        Used by the flush to anchor a progress total when a window spans more
        than one cap-sized slice, so a long multi-slice drain can log N/total
        instead of looking stuck."""
        return WatchActionRun.objects.filter(account_id=self.account_id, action_id=action_id, state=_PENDING).count()

    def complete_batch(
        self,
        run_ids: builtins.list[str],
        *,
        state: WatchActionRunState,
        result: dict | None = None,
        error: str = "",
        delivery_id: str = "",
        kind: str | None = None,
        now: datetime | None = None,
    ) -> int:
        """Mark a digest batch terminal in one UPDATE. Guarded on state ==
        PENDING so a double flush can't re-complete an already-emitted batch.
        Returns the count written.

        `result` (the rendered digest) is written onto EVERY run in the batch,
        so the runs audit shows the same batch render N times. Acceptable
        duplication for a v1 audit ; the batch is the unit of delivery, the
        per-run row is just its membership. `delivery_id` links every run to the
        WatchActionDelivery (HTTP call) that carried the batch (blank for the
        local log, which makes no call). `kind` re-stamps each run to the kind
        that ACTUALLY ran (the flush dispatches by the action's current kind,
        which may have been edited since enqueue), keeping run.kind in step with
        the written `result` shape ; mirrors `complete()`. Chunked under the DB's
        per-statement parameter ceiling (common.db.ID_IN_CHUNK) so a large batch
        can't crash `id__in`."""
        ts = now or timezone.now()
        fields: dict = {
            "state": state.value,
            "result": result or {},
            "error": error,
            "completed_at": ts,
            "updated_at": ts,
        }
        if delivery_id:
            fields["delivery_id"] = delivery_id
        if kind:
            fields["kind"] = kind
        written = 0
        for chunk in itertools.batched(run_ids, ID_IN_CHUNK, strict=False):
            written += WatchActionRun.objects.filter(id__in=chunk, account_id=self.account_id, state=_PENDING).update(
                **fields
            )
        return written

    def fail_batch(self, run_ids: builtins.list[str], *, now: datetime | None = None) -> int:
        """Record a TRANSIENT digest-flush failure: burn one attempt on each
        still-pending run in the batch. A run that reaches
        WATCH_RUN_MAX_ATTEMPTS is marked terminally FAILED so it drops out of
        the next gather (`digest_batch` reads PENDING only), draining the
        window ; a run still under the cap stays PENDING for the next flush to
        retry. This is the digest analog of the instant path's claim-time
        attempts cap (claim_due / reap_stale never touch digest runs, so the
        cap has to be enforced here) — a persistently-down destination drains
        to a terminal state instead of re-emitting a growing batch forever.
        Returns the count newly exhausted (moved to terminal FAILED).

        The cap is PER RUN, and a straggler joining a still-open failing
        window starts at attempts 0 ; so a continuous trickle into a down
        destination can keep the WINDOW from ever fully terminal-draining
        (there's always a young run), even though each individual run is
        bounded and terminates. Acceptable: bounded per item is the contract,
        and a steady arrival stream means the watch is live, not stuck.

        Chunked under the DB's per-statement parameter ceiling
        (common.db.ID_IN_CHUNK) ; the increment + exhaust pair runs per chunk
        so each run is handled once."""
        ts = now or timezone.now()
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        exhausted = 0
        for chunk in itertools.batched(run_ids, ID_IN_CHUNK, strict=False):
            WatchActionRun.objects.filter(id__in=chunk, account_id=self.account_id, state=_PENDING).update(
                attempts=F("attempts") + 1, error=run_messages.TRANSIENT, updated_at=ts
            )
            exhausted += WatchActionRun.objects.filter(
                id__in=chunk, account_id=self.account_id, state=_PENDING, attempts__gte=max_attempts
            ).update(state=_FAILED, error=run_messages.TRANSIENT_EXHAUSTED, completed_at=ts, updated_at=ts)
        return exhausted
