import logging
import time
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService

logger = logging.getLogger("watches")

# Stable display order for state breakdowns: the run-state lifecycle order
# (succeeded first), derived from the enum so it can't drift and matches
# `watch action activity`. An unknown state sorts last.
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
# machines, drop back to plain BaseCommand so N workers run at once.
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

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        verbose = bool(options.get("verbose"))
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
                    "Draining %d due run%s… You'll see progress every ~minute.",
                    total_due,
                    "s" if total_due != 1 else "",
                )
            else:
                logger.info("No runs due.")
        t_start = time.monotonic()
        last_checkpoint = t_start
        processed = 0

        for processed, run in enumerate(WatchActionRunService.Global.claim_due(now=now), start=1):
            # Per-run try/except: an UNEXPECTED error (e.g. the commit
            # itself) must not abort the pass. The run stays RUNNING and the
            # next reap retries it; one bad run never starves the queue.
            try:
                outcome = WatchDrainOperation(run, now=now).run()
            except Exception as exc:
                infra_failed += 1
                logger.exception("drain failed run=%s: %s", run.id, exc)
                detail = f"failed: {type(exc).__name__}: {exc}"
            else:
                if outcome is None:
                    # Claim lost: this run was reaped + re-claimed by another
                    # drain mid-judge ; the fresh winner owns the result + the
                    # chain advance, so we drop ours (don't count, don't advance).
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
                # `action=` so an operator can pivot to `watch action activity`.
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
