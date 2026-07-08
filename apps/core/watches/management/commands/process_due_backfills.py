import logging
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from watches.operations.backfill import WatchBackfillOperation
from watches.services import WatchActionBackfillService

logger = logging.getLogger("watches")


# Single-flight is a convenience (don't stack passes on one box), NOT a
# correctness requirement: the CAS claim already makes concurrent crons safe
# (they split the queue). Ordered BEFORE process_due_runs where the tick is
# SERIAL (make local-tick, scripts/quickstart/tick.sh), so a job's freshly-enqueued
# runs drain in that same tick; under independent tickers (make up-jobs) there's
# no ordering guarantee and the runs simply drain on the next drain pass. Either
# way it's correct, just a latency difference.
class Command(SingleFlightCommand):
    help = "Backfill pass: reap stale jobs, then claim + run every due backfill (select/delete/enqueue). Scheduler entry point."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()

        # Reap first so a crashed-worker job (stuck RUNNING) rejoins the pool
        # before this pass claims, instead of waiting a tick.
        reaped = WatchActionBackfillService.Global.reap_stale(now=now)
        if reaped:
            logger.info("reaped %d stale backfill job(s)", reaped)

        done = 0
        failed = 0
        infra_failed = 0
        for job in WatchActionBackfillService.Global.claim_due(now=now):
            # Per-job try/except: an UNEXPECTED error must not abort the pass. The
            # job stays RUNNING and the reaper retries it (setup is idempotent +
            # delete-once guarded); one bad job never starves the queue.
            try:
                counts = WatchBackfillOperation(job, now=now).run()
            except Exception as exc:
                infra_failed += 1
                logger.exception("backfill failed job=%s: %s", job.id, exc)
                continue
            if counts is None:
                failed += 1
                logger.warning("backfill job=%s failed permanently: %s", job.id, job.error)
                continue
            done += 1
            logger.info(
                "backfill job=%s done: matched=%d present=%d pruned=%d deleted=%d enqueued=%d",
                job.id,
                counts.matched,
                counts.present,
                counts.pruned,
                counts.deleted,
                counts.enqueued,
            )

        if done or failed or infra_failed or reaped:
            logger.info(
                "Reaped %d, ran %d backfill job(s) done, %d failed, %d infra-failed", reaped, done, failed, infra_failed
            )
