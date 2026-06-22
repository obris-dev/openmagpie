"""Cross-tenant drain operations for the `process_due_runs` cron: the
`_due_runs` filter (single source of truth for "due") + `WatchActionRunGlobal`
(reap stale, claim due, count due). Account-agnostic by design — the drain is
a Global scan."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Exists, F, OuterRef
from django.utils import timezone

from watches import run_messages
from watches.models import WatchActionDigestWindow, WatchActionRun

from ._common import _CLAIMABLE, _FAILED, _RUNNING


def _due_runs(ts: datetime):
    """Runs eligible to drain at `ts`: claimable state, under the attempts
    cap, scheduled time elapsed, NOT a digest action's (those are the flush's,
    derived from the action's window row). SINGLE source of truth for "due"
    so `count_due` can't drift from what `claim_due` yields. Unordered."""
    has_window = Exists(
        WatchActionDigestWindow.objects.filter(account_id=OuterRef("account_id"), action_id=OuterRef("action_id"))
    )
    return WatchActionRun.objects.filter(
        state__in=_CLAIMABLE,
        attempts__lt=settings.WATCH_RUN_MAX_ATTEMPTS,
        scheduled_at__lte=ts,
    ).filter(~has_window)


class WatchActionRunGlobal:
    """Cross-tenant run operations for the drain cron. Static methods."""

    @staticmethod
    def reap_stale(*, now: datetime | None = None) -> int:
        """Reset runs stuck in RUNNING past WATCH_RUN_STALE_SECONDS to
        FAILED (presumed crashed worker). Their attempt was already burned
        at claim, so this can't loop forever. Returns the count reaped.
        Runs first in each drain pass so a crashed run rejoins the retry
        pool (or hits the attempts cap and stays FAILED terminally).

        Branches on the attempts cap so the message tells the truth: a run
        crashed on its FINAL attempt won't be re-claimed (claim_due needs
        attempts < MAX), so it gets a terminal message, not 'will retry'."""
        ts = now or timezone.now()
        cutoff = ts - timedelta(seconds=settings.WATCH_RUN_STALE_SECONDS)
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        stale = WatchActionRun.objects.filter(state=_RUNNING, started_at__lt=cutoff)
        # Exhausted: timed out with no retries left -> terminal. completed_at
        # set (it's truly done), honest message (claim_due skips it).
        exhausted = stale.filter(attempts__gte=max_attempts).update(
            state=_FAILED,
            error=run_messages.TIMED_OUT_EXHAUSTED,
            completed_at=ts,
        )
        # Retryable: attempts left -> rejoins the pool. completed_at cleared
        # (not done yet), 'will retry' message. Disjoint from `exhausted` on
        # attempts, so order is irrelevant.
        retryable = stale.filter(attempts__lt=max_attempts).update(
            state=_FAILED,
            error=run_messages.TIMED_OUT,
            completed_at=None,
        )
        return exhausted + retryable

    @staticmethod
    def claim_due(*, now: datetime | None = None) -> Iterator[WatchActionRun]:
        """Yield runs due now, each already CLAIMED (CAS to RUNNING).

        "Due" = claimable state (pending / retryable-failed), under the
        attempts cap, with scheduled_at elapsed. Each candidate is claimed
        by a conditional UPDATE keyed on its still-claimable state ; only
        a row we actually flipped (updated == 1) is yielded, so two
        concurrent drains never both execute the same run. The candidate
        snapshot is read up front but the CAS re-checks state at flip time,
        so a row another drain grabbed in between is simply skipped."""
        ts = now or timezone.now()
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        # `_due_runs` carries the "due" filter (incl. the digest-action
        # anti-join: a correlated ~Exists on the window table — one SQL
        # statement, no ids pulled into Python). Ordered oldest-first and
        # streamed (chunk_size) so a big backlog never materializes.
        candidates = _due_runs(ts).order_by("scheduled_at").iterator(chunk_size=100)
        for run in candidates:
            claimed = WatchActionRun.objects.filter(id=run.id, state=run.state, attempts__lt=max_attempts).update(
                state=_RUNNING, started_at=ts, attempts=F("attempts") + 1
            )
            if claimed:
                run.refresh_from_db()
                yield run

    @staticmethod
    def count_due(*, now: datetime | None = None) -> int:
        """How many runs `claim_due` would yield at `now` (same filter, no
        claim). Used only to size the drain's progress/ETA line ; it's a
        pre-pass snapshot, so the live count can drift slightly (concurrent
        drains claiming rows, or rows falling due mid-pass)."""
        return _due_runs(now or timezone.now()).count()

    @staticmethod
    def count_by_state_since(since: datetime) -> dict[str, int]:
        """{run state: count} for runs completed since `since`, all accounts
        (the heartbeat's 24h rollup)."""
        rows = WatchActionRun.objects.filter(completed_at__gte=since).values("state").annotate(n=Count("id"))
        return {row["state"]: row["n"] for row in rows}
