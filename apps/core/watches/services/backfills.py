"""WatchActionBackfillService: account-scoped backfill-job reads + writes, plus a
cross-tenant `Global` (claim due / reap stale) for the `process_due_backfills` cron.

A backfill job is a queued request to re-run one action over the previous step's
passes (see the model). This service persists the job (the endpoint's fast path)
and the cron's Global claims/reaps it; the heavy select/delete/enqueue is
`WatchBackfillOperation`, not here. Mirrors the run drain's Global (claim by CAS,
reap stale RUNNING) so backfill jobs get the same crash-recovery guarantees.
"""

from __future__ import annotations

import builtins
from collections.abc import Iterator
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from openmagpie_schema.run_windows import RunWindows
from openmagpie_schema.watch_enums import BACKFILL_CLAIMABLE_STATES, WatchActionBackfillState
from watches import run_messages
from watches.models import WatchActionBackfill

_PENDING = WatchActionBackfillState.PENDING.value
_RUNNING = WatchActionBackfillState.RUNNING.value
_FAILED = WatchActionBackfillState.FAILED.value
_CLAIMABLE = tuple(s.value for s in BACKFILL_CLAIMABLE_STATES)


class WatchActionBackfillGlobal:
    """Cross-tenant backfill-job operations for the cron. Static methods; account-
    agnostic (the cron is a Global scan, like the run drain's Global)."""

    @staticmethod
    def reap_stale(*, now: datetime | None = None) -> int:
        """Reset jobs stuck in RUNNING past WATCH_BACKFILL_STALE_SECONDS to FAILED
        (presumed crashed worker). Retryable ones (under the attempts cap) get
        completed_at cleared so claim_due re-picks them (FAILED is claimable); the
        setup is idempotent + delete-once guarded, so a retry is safe. Exhausted ones
        get completed_at stamped -> terminal. Returns the count reaped."""
        ts = now or timezone.now()
        cutoff = ts - timedelta(seconds=settings.WATCH_BACKFILL_STALE_SECONDS)
        max_attempts = settings.WATCH_BACKFILL_MAX_ATTEMPTS
        stale = WatchActionBackfill.objects.filter(state=_RUNNING, started_at__lt=cutoff)
        # .update() bypasses auto_now, so updated_at is set explicitly (like complete()).
        exhausted = stale.filter(attempts__gte=max_attempts).update(
            state=_FAILED, error=run_messages.BACKFILL_TIMED_OUT_EXHAUSTED, completed_at=ts, updated_at=ts
        )
        retryable = stale.filter(attempts__lt=max_attempts).update(
            state=_FAILED, error=run_messages.BACKFILL_TIMED_OUT, completed_at=None, updated_at=ts
        )
        return exhausted + retryable

    @staticmethod
    def claim_due(*, now: datetime | None = None) -> Iterator[WatchActionBackfill]:
        """Yield jobs due now, each already CLAIMED (CAS to RUNNING). "Due" =
        claimable state (PENDING / retryable-FAILED), under the attempts cap,
        scheduled_at elapsed. The conditional UPDATE keyed on the still-claimable
        state means two concurrent crons never both run the same job."""
        ts = now or timezone.now()
        max_attempts = settings.WATCH_BACKFILL_MAX_ATTEMPTS
        candidates = (
            WatchActionBackfill.objects.filter(state__in=_CLAIMABLE, attempts__lt=max_attempts, scheduled_at__lte=ts)
            .order_by("scheduled_at")
            .iterator(chunk_size=50)
        )
        for job in candidates:
            claimed = WatchActionBackfill.objects.filter(id=job.id, state=job.state, attempts__lt=max_attempts).update(
                state=_RUNNING, started_at=ts, attempts=F("attempts") + 1, updated_at=ts
            )
            if claimed:
                job.refresh_from_db()
                yield job


class WatchActionBackfillService:
    """Account-scoped backfill-job reads + writes; `Global` is the cron's
    cross-tenant claim/reap surface (mirrors WatchActionRunService)."""

    Global = WatchActionBackfillGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionBackfillService requires account_id")
        self.account_id = account_id

    def create(
        self,
        *,
        watch_id: str,
        target_action_id: str,
        source_action_id: str,
        source_is_head: bool,
        kind: str,
        replace: bool,
        windows: RunWindows,
        scheduled_at: datetime,
    ) -> WatchActionBackfill:
        """Persist a PENDING backfill job (the POST's fast path, no heavy work).
        `windows` are the ALREADY-resolved absolute bounds (the endpoint resolved the
        raw request values against the server clock); they're pinned onto the row so
        a relative window doesn't drift before the cron runs the job."""
        return WatchActionBackfill.objects.create(
            account_id=self.account_id,
            watch_id=watch_id,
            target_action_id=target_action_id,
            source_action_id=source_action_id,
            source_is_head=source_is_head,
            kind=kind,
            replace=replace,
            occurred_since=windows.get("occurred_since"),
            occurred_until=windows.get("occurred_until"),
            completed_since=windows.get("completed_since"),
            completed_until=windows.get("completed_until"),
            state=_PENDING,
            scheduled_at=scheduled_at,
        )

    def get(self, job_id: str, /) -> WatchActionBackfill:
        """One backfill job by id (account-scoped). Raises
        WatchActionBackfill.DoesNotExist if missing / another account's (the
        status endpoint maps that to 404)."""
        return WatchActionBackfill.objects.get(id=job_id, account_id=self.account_id)

    def list(self, *, after: str | None = None, limit: int = 50) -> builtins.list[WatchActionBackfill]:
        """This account's backfill jobs, newest-first (ULID pk). Cursor-paginated:
        `after=<id>` fetches rows older than that id. Rides the
        `(account_id, id)` index."""
        qs = WatchActionBackfill.objects.filter(account_id=self.account_id)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])
