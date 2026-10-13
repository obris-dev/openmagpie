import logging
from typing import Any

from django.utils import timezone

from common.commands import SingleFlightCommand
from common.locks import digest_flush_lock
from watches.operations.digest_flush import WatchDigestFlushOperation
from watches.services import WatchDigestWindowService

logger = logging.getLogger("watches")


class Command(SingleFlightCommand):
    help = "Flush pass: emit each due digest window's accumulated batch. Scheduler entry point."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--quiet", action="store_true", help="Suppress per-window progress; print only the summary."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        quiet = bool(options.get("quiet"))
        now = timezone.now()
        emitted = 0
        empty = 0
        skipped = 0
        failed = 0
        by_state: dict[str, int] = {}

        for window in WatchDigestWindowService.Global.iter_due(now=now):
            # The per-window fence: the gather has no claim of its own, so two
            # passes on one window (a lapsed job lease) would both emit. A held
            # lock means another pass is on it; the next tick retries. Acquired
            # outside the operation's transactions (the emit is a network call).
            with digest_flush_lock(str(window.action_id)) as lease:
                if not lease:
                    skipped += 1
                    if not quiet:
                        self.stdout.write(f"  action {window.action_id}: skipped (flush held by another pass)")
                    continue
                # Per-window try/except: one window raising must not abort the pass.
                try:
                    outcome = WatchDigestFlushOperation(window, now=now).run()
                except Exception as exc:
                    failed += 1
                    logger.exception("digest flush failed action=%s: %s", window.action_id, exc)
                    if not quiet:
                        self.stdout.write(f"  action {window.action_id}: failed: {type(exc).__name__}: {exc}")
                    continue
            if outcome is None:
                empty += 1
                continue
            emitted += 1
            by_state[outcome.state.value] = by_state.get(outcome.state.value, 0) + 1
            if not quiet:
                self.stdout.write(f"  action {window.action_id}: {outcome.state.value}")

        breakdown = ", ".join(f"{n} {s}" for s, n in sorted(by_state.items())) or "none"
        self.stdout.write(
            f"\nFlushed {emitted} window(s) ({breakdown}), {empty} empty, {skipped} skipped, {failed} failed"
        )
