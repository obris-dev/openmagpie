"""LeaseRenewer: keep a `named_lock` lease alive for the life of a unit of work.

A lock's `timeout` is a LIVENESS window (see `common.locks`): a holder that
runs longer than it must renew as it goes or the lease lapses under it. The
renewer does that from a daemon thread so the holder's own code carries no
lease concern: wrap the work in `with LeaseRenewer(lease, ...)` and the lease
is re-stamped every third of its window until the block exits. Used by
`SingleFlightCommand` for the job lock (one per pass); generic enough for any
`named_lock` lease (the per-feed poll lease is the named follow-up).

The thread shares the process's fate, which is the point: a hard kill
(SIGKILL / OOM / the container recreated under an exec'd pass) stops the
renewals and the lease lapses after one window, so a dead holder frees its
lock on its own. Liveness is not progress, though, so `max_hold` caps how long
one holder may keep renewing (a hung-but-alive holder cannot strand the lock
forever). A renewal that RAISES (the default cache is DB-backed, and a dropped
connection raises rather than returning False) is retried next tick; the lease
counts as lost only once a whole window has passed without a successful
renewal, the same judgment a `False` return expresses. On loss the renewer
logs and exits; the holder's work carries on, because deciding where it is
safe to stop is the holder's call (see `common.commands` on why single-flight
is a convenience, not a correctness bound). Settings are read by the caller
and passed in; this module is settings-free.
"""

import contextlib
import logging
import threading
import time
from typing import Protocol, Self

from django.db import connections

logger = logging.getLogger(__name__)

# Thread-name prefix every renewer uses; shared with the tests that assert no
# renewer outlives its holder, so the two can't drift (one source of truth).
RENEWER_THREAD_PREFIX = "lease-renew:"


def renew_interval_seconds(timeout: float) -> float:
    """How often the renewer re-stamps a lease of `timeout` seconds: a third of
    the window, so two consecutive renewals can be late (a slow cache round
    trip, a GC pause) before the lease lapses. Floored at 1s for tiny test
    timeouts, which keeps the thread from spinning."""
    return max(1.0, timeout / 3)


class Renewable(Protocol):
    """What `LeaseRenewer` needs of a lease: `LockLease.renew`'s contract (True
    while still ours, False once lost; may raise when the cache is unreachable).
    A Protocol, not `LockLease` itself, so the renewer is unit-testable against
    a counting fake with no cache."""

    def renew(self) -> bool: ...


class LeaseRenewer(threading.Thread):
    """A daemon thread that re-stamps `lease` every `renew_interval_seconds`
    until stopped (the `with` exit), until the holder has kept it for
    `max_hold` seconds, or until the lease is lost. `label` names the holder in
    log lines (a job name, a feed id). The thread opens its own DB connection
    when the cache is DB-backed, so it closes it on the way out; see the module
    docstring for the failure semantics."""

    def __init__(
        self,
        lease: Renewable,
        *,
        label: str,
        lease_seconds: float,
        max_hold: float,
        interval: float | None = None,
    ) -> None:
        super().__init__(name=f"{RENEWER_THREAD_PREFIX}{label}", daemon=True)
        self._lease = lease
        self._label = label
        self._lease_seconds = lease_seconds
        # `interval` is a test seam (sub-second ticks against a fake lease);
        # production callers leave it None and get a third of the window.
        self._interval = renew_interval_seconds(lease_seconds) if interval is None else interval
        self._max_hold = max_hold
        self._stop_event = threading.Event()
        self._started_at = time.monotonic()

    def run(self) -> None:
        last_ok = self._started_at
        try:
            while not self._stop_event.wait(self._interval):
                now = time.monotonic()
                if now - self._started_at >= self._max_hold:
                    logger.warning(
                        "%s: has held its lease for %ss (max hold); no longer renewing, so the lease lapses "
                        "within %ss and the next holder may run alongside this one",
                        self._label,
                        self._max_hold,
                        self._lease_seconds,
                    )
                    return
                try:
                    still_ours = self._lease.renew()
                except Exception:
                    # Broad on purpose: the backend behind `Renewable` is unknown
                    # here (DB, Redis, locmem) and the only action is to retry next
                    # tick; exc_info keeps a programming error visible rather than
                    # reading as a flaky cache. Django never reconnects a dead
                    # connection object, so drop this thread's connections and let
                    # the next tick open fresh ones.
                    logger.warning("%s: lease renewal failed; retrying next tick", self._label, exc_info=True)
                    with contextlib.suppress(Exception):
                        connections.close_all()
                    # Lost once a whole window has passed unrenewed: the key has
                    # expired by then whether or not the cache can tell us so.
                    still_ours = now - last_ok < self._lease_seconds
                else:
                    last_ok = now
                if not still_ours:
                    logger.warning(
                        "%s: lease lost (not renewed within %ss); another holder may start alongside this one",
                        self._label,
                        self._lease_seconds,
                    )
                    return
        finally:
            # Thread-local connections only (Django's are per-thread). Suppressed
            # so the teardown of a dead connection can't be a new way to die.
            with contextlib.suppress(Exception):
                connections.close_all()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop_event.set()
        self.join()
