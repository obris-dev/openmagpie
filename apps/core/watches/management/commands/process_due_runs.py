import logging
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import connections
from django.utils import timezone

from common.commands import SingleFlightCommand
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.actions.protocol import ActionResult
from watches.models import WatchActionRun
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService

logger = logging.getLogger("watches")

# One finished run: the drain's ActionResult (None = claim lost mid-judge) paired
# with the exception it raised (None on success). Exactly one side is meaningful.
_DrainOutcome = tuple[ActionResult | None, Exception | None]

# Stable display order for state breakdowns: the run-state lifecycle order
# (succeeded first), derived from the enum so it can't drift and matches
# `magpie activity summary`. An unknown state sorts last.
_STATE_ORDER = {s.value: i for i, s in enumerate(WatchActionRunState)}

# Seconds between progress checkpoints (the cadence when neither --verbose
# nor --quiet is set). TIME-based, not count-based: per-run cost swings
# wildly (a gated filter is ~1s, a full LLM call ~120s), so "every N runs"
# would print every few seconds OR every few HOURS depending on the work. A
# wall-clock interval gives steady feedback either way ; checked after each
# run, so the effective cadence is max(run_time, interval) — fast runs batch
# into ~1/min, slow runs land ~one checkpoint each. A constant, not a flag.
_CHECKPOINT_SECONDS = 60  # 1 minute


def _fmt_duration(seconds: float) -> str:
    """Coarse h/m/s for the progress line ; sub-minute keeps seconds."""
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def _progress(processed: int, total: int, t_start: float) -> str:
    """`[12/345, ~10m04s left]` — ETA = running average wall-time per run
    times the runs still queued. `total` is the due count snapshotted at
    pass start ; if more fell due mid-pass the remaining floors at 0 (ETA
    just reads ~0s near the end) rather than going negative."""
    elapsed = time.monotonic() - t_start
    avg = elapsed / processed if processed else 0.0
    remaining = max(total - processed, 0)
    return f"[{processed}/{total}, ~{_fmt_duration(avg * remaining)} left]"


def _breakdown(tally: dict[str, int]) -> str:
    """`3 succeeded, 342 gated` — state counts in lifecycle order (commas,
    not dots), `none` when empty. Shared by the checkpoint + final summary."""
    ordered = sorted(tally.items(), key=lambda kv: _STATE_ORDER.get(kv[0], len(_STATE_ORDER)))
    return ", ".join(f"{n} {s}" for s, n in ordered) or "none"


# Single-flight here is a convenience (don't stack passes on one box), NOT
# a correctness requirement: the CAS claim already makes concurrent drains
# safe ; they split the queue. To scale the drain horizontally across
# machines, drop back to plain BaseCommand so N workers run at once. Within
# one box, --concurrency N runs N judges at once over that same safe claim.
class Command(SingleFlightCommand):
    help = (
        "Drain pass: reap stale runs, then claim + execute every due run, advancing the chain. Scheduler entry point."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only the final summary ; no progress checkpoints.",
        )
        parser.add_argument(
            "--verbose",  # NB: not -v (Django reserves that for --verbosity)
            action="store_true",
            help="One line per run (noisy) instead of the periodic checkpoint.",
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=None,
            help=(
                "How many runs to drain at once (default 1 = serial). N>1 runs the "
                "network-bound judge in a thread pool while the CAS claim stays serial, "
                "so N is the number of concurrent engine calls. Keep N at or below your "
                "engine's rate limit (a 429 is retried with backoff up to ENGINE_MAX_RETRIES, "
                "then a retryable FAILED). "
                "Unset, it reads settings.WATCH_RUN_DRAIN_CONCURRENCY "
                "(env WATCH_RUN_DRAIN_CONCURRENCY), so the ticker scales via .env."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        verbose = bool(options.get("verbose"))
        # Explicit --concurrency wins ; unset (None) falls back to the env-backed
        # setting so the ticker scales via .env without a flag. Floor at 1.
        requested = options.get("concurrency")
        if requested is None:
            requested = settings.WATCH_RUN_DRAIN_CONCURRENCY
        concurrency = max(1, int(requested))
        now = timezone.now()

        # Reap first so a crashed-worker run (stuck RUNNING) rejoins the
        # retry pool before this pass claims, instead of waiting a tick.
        reaped = WatchActionRunService.Global.reap_stale(now=now)
        if reaped and not quiet:
            logger.info("reaped %d stale run(s)", reaped)

        executed = 0
        infra_failed = 0
        lost_claims = 0
        by_state: dict[str, int] = {}
        # Per-action state tallies for the CURRENT checkpoint window, reset
        # after each checkpoint — so a checkpoint reports what happened in
        # THIS chunk (recent behavior, catches a regime change), not an
        # ever-growing running total. The grand total is the final summary.
        chunk: dict[str, dict[str, int]] = {}

        # Snapshot the due count up front so the checkpoint can show position
        # + ETA. A semantic_filter run is a slow (~120s) LLM call, so a
        # backlog can take hours. Cheap COUNT, no rows materialized.
        total_due = WatchActionRunService.Global.count_due(now=now)
        # First paint: announce the work + count immediately, BEFORE the
        # first (potentially slow) run, so the operator isn't staring at a
        # blank terminal wondering if it hung. A pass can be far slower than the
        # rest of the quickstart, so set that expectation (and the ~1-minute
        # checkpoint cadence) here, so the quiet gap before the first "[n/total]"
        # reads as expected, not hung. ("~minute" mirrors _CHECKPOINT_SECONDS=60.)
        if not quiet:
            if total_due:
                logger.info(
                    "Draining %d due run%s%s… You'll see progress every ~minute.",
                    total_due,
                    "s" if total_due != 1 else "",
                    f" at concurrency {concurrency}" if concurrency > 1 else "",
                )
            else:
                logger.info("No runs due.")
        t_start = time.monotonic()
        last_checkpoint = t_start
        processed = 0

        # Serial (concurrency=1) and threaded paths feed ONE consumer loop, so the
        # tally, progress, and checkpoint logic below is identical for both. Each
        # yields (run, (outcome, exc)) with the drain already run by _safe_drain.
        claimer = WatchActionRunService.Global.claim_due(now=now)
        completions = (
            self._concurrent_completions(claimer, now, concurrency)
            if concurrency > 1
            else ((run, self._safe_drain(run, now)) for run in claimer)
        )
        for processed, (run, (outcome, exc)) in enumerate(completions, start=1):
            if exc is not None:
                # An UNEXPECTED error (e.g. the commit itself) never aborts the pass:
                # the run stays RUNNING and the next reap retries it. exc_info=exc
                # attaches the traceback even though we're past the except block.
                infra_failed += 1
                logger.error("drain failed run=%s: %s", run.id, exc, exc_info=exc)
                detail = f"failed: {type(exc).__name__}: {exc}"
            elif outcome is None:
                # Claim lost: this run was reaped + re-claimed by another drain
                # mid-judge ; the fresh winner owns the result + the chain advance,
                # so we drop ours (don't count, don't advance).
                lost_claims += 1
                detail = "claim lost (handled by another worker)"
            else:
                executed += 1
                detail = outcome.state.value
                by_state[detail] = by_state.get(detail, 0) + 1
                tally = chunk.setdefault(str(run.action_id), {})
                tally[detail] = tally.get(detail, 0) + 1
            if quiet:
                continue
            if verbose:
                # `action=` so an operator can pivot to `magpie activity list --action`.
                logger.info(
                    "%s run=%s action=%s: %s", _progress(processed, total_due, t_start), run.id, run.action_id, detail
                )
            elif time.monotonic() - last_checkpoint >= _CHECKPOINT_SECONDS:
                # A checkpoint every _CHECKPOINT_SECONDS so feedback is steady
                # whatever the per-run cost (the first-paint header already
                # covered liveness + scale). chunk + last_checkpoint reset
                # together, so each reports exactly the runs since the previous.
                self._checkpoint(processed, total_due, t_start, chunk)
                chunk = {}
                last_checkpoint = time.monotonic()

        # Flush the final partial window (the per-action view the one-line
        # summary doesn't carry), then the global totals.
        if not quiet and not verbose and chunk:
            self._checkpoint(processed, total_due, t_start, chunk)
        logger.info(
            "Reaped %d, executed %d run(s) (%s), %d claim(s) lost, %d infra-failed in %s",
            reaped,
            executed,
            _breakdown(by_state),
            lost_claims,
            infra_failed,
            _fmt_duration(time.monotonic() - t_start),
        )

    def _checkpoint(self, processed: int, total: int, t_start: float, tallies: dict[str, dict[str, int]]) -> None:
        """One log record: the progress line + this window's per-action state
        breakdown (delta). A single multi-line message so the breakdown rows
        sit, un-timestamped, under the one checkpoint instant (the logger
        stamps only the first line)."""
        lines = [_progress(processed, total, t_start)]
        lines += [f"  action={action_id}: {_breakdown(tally)}" for action_id, tally in tallies.items()]
        logger.info("%s", "\n".join(lines))

    def _safe_drain(self, run: WatchActionRun, now: datetime) -> _DrainOutcome:
        """Execute one claimed run, returning (outcome, None) or (None, exc). Never
        raises: one bad run must not abort the pass (serial), and a thread's escaping
        exception would be buried in the pool (concurrent), so both get the failure as
        a value. `outcome` is the drain's ActionResult, or None when the claim was lost
        mid-judge. Deliberately does NOT touch connections: the serial path runs this on
        the MAIN thread while it iterates claim_due's server-side cursor, so closing that
        connection (CONN_MAX_AGE) would break the cursor's next fetch. Pool-thread
        connection hygiene lives in _drain_worker instead."""
        try:
            return WatchDrainOperation(run, now=now).run(), None
        except Exception as exc:  # deliberate blanket catch: isolate one run's failure
            return None, exc

    def _drain_worker(self, run: WatchActionRun, now: datetime) -> _DrainOutcome:
        """Pool-thread entry point: run _safe_drain, then close this worker's DB
        connections so a pooled thread never leaves one open between tasks. Only the
        pool threads do this ; the main thread must NOT (it holds claim_due's server-side
        cursor, see _safe_drain). `connections` is thread-local, so close_all() here
        touches only this worker's own connections, never the main thread's cursor."""
        try:
            return self._safe_drain(run, now)
        finally:
            connections.close_all()

    def _concurrent_completions(
        self, claimer: Iterator[WatchActionRun], now: datetime, concurrency: int
    ) -> Iterator[tuple[WatchActionRun, _DrainOutcome]]:
        """Drain up to `concurrency` runs at once, yielding (run, (outcome, exc)) as
        each finishes. The claim (a fast CAS UPDATE) stays serial on this thread ; only
        the slow, network-bound judge runs in the pool, so N is effectively the number
        of concurrent engine calls. Bounded: a new run is claimed only as a slot frees,
        so at most N sit RUNNING at once (never the whole backlog claimed up front).

        On abort (Ctrl-C, or the consumer loop raising) the finally cancels QUEUED
        futures immediately, but a judge already running can't be interrupted: the
        pool's non-daemon threads are joined at interpreter exit, so the process still
        waits out the in-flight judges (up to ~a judge timeout) before exiting. Nothing
        is lost either way: a judge that finishes writes its own terminal state, and a
        run cut short by a hard SIGKILL stays RUNNING for the reaper to reclaim (CAS-safe)."""
        pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="drain")
        try:
            in_flight: dict[Any, WatchActionRun] = {}

            def fill() -> None:
                while len(in_flight) < concurrency:
                    try:
                        run = next(claimer)
                    except StopIteration:
                        return
                    in_flight[pool.submit(self._drain_worker, run, now)] = run

            fill()
            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done:
                    run = in_flight.pop(fut)
                    result: _DrainOutcome = fut.result()  # _drain_worker never raises
                    fill()  # refill the freed slot before handing this result back
                    yield run, result
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
