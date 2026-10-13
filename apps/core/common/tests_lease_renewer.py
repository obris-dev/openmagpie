"""`LeaseRenewer` on its own, against a fake lease: the interval rule and the
thread's exits (stopped by the `with`, lease lost, max hold reached, a raising
renewal retried then lost), with no command and no cache involved. The
`interval` seam makes these sub-second: 20ms ticks against a 150ms lease; the
real-TTL proofs stay in `tests_job_lease*.py`. Counts are asserted as ranges
where scheduler jitter could move a tick by one."""

import time
from collections.abc import Iterable

from django.test import SimpleTestCase

from common.lease_renewer import LeaseRenewer, renew_interval_seconds

_TICK = 0.02
_LEASE = 0.15
_FOREVER = 86400


class _FakeLease:
    """Counts renewals; flips to lost after `lose_after` of them; RAISES on the
    renewal numbers in `raise_on` (a cache outage, as the DB backend reports it)."""

    def __init__(self, lose_after: int | None = None, raise_on: Iterable[int] = ()) -> None:
        self.renewals = 0
        self._lose_after = lose_after
        self._raise_on = set(raise_on)

    def renew(self) -> bool:
        self.renewals += 1
        if self.renewals in self._raise_on:
            raise RuntimeError("cache down")
        return self._lose_after is None or self.renewals <= self._lose_after


def _renewer(lease: _FakeLease, *, max_hold: float = _FOREVER) -> LeaseRenewer:
    return LeaseRenewer(lease, label="unit", lease_seconds=_LEASE, max_hold=max_hold, interval=_TICK)


class LeaseRenewerTests(SimpleTestCase):
    def test_renew_interval_is_a_third_of_the_window_floored_at_one_second(self) -> None:
        self.assertEqual(renew_interval_seconds(300), 100)
        self.assertEqual(renew_interval_seconds(3), 1)
        self.assertEqual(renew_interval_seconds(1), 1)  # never below 1s (no spin)

    def test_renews_on_its_interval_until_stopped(self) -> None:
        lease = _FakeLease()
        with _renewer(lease) as renewer:
            time.sleep(0.2)  # ~10 ticks
            self.assertTrue(renewer.is_alive())
        self.assertFalse(renewer.is_alive())  # joined on exit
        self.assertGreaterEqual(lease.renewals, 5)

    def test_exits_once_the_lease_is_lost(self) -> None:
        lease = _FakeLease(lose_after=1)
        with (
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
            _renewer(lease) as renewer,
        ):
            time.sleep(0.2)
            self.assertFalse(renewer.is_alive())  # gave up on its own, before the exit
        self.assertEqual(lease.renewals, 2)  # the second renew was the lost one; no tick after it
        self.assertIn("lease lost", logs.output[0])

    def test_exits_once_the_max_hold_is_reached(self) -> None:
        lease = _FakeLease()
        with (
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
            _renewer(lease, max_hold=0.1) as renewer,  # ~4 renewing ticks, then the ~0.1s tick hits the cap
        ):
            time.sleep(0.3)
            self.assertFalse(renewer.is_alive())
        self.assertTrue(1 <= lease.renewals <= 5, lease.renewals)  # renewed, then stopped at the cap
        self.assertIn("max hold", logs.output[0])

    def test_a_raising_renew_is_retried_not_fatal(self) -> None:
        """A one-tick cache outage (the DB backend RAISES on a dropped connection)
        must not kill the renewer: it logs, drops its connections, and renews again
        on the next tick, still inside the lease window."""
        lease = _FakeLease(raise_on={1})
        with (
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
            _renewer(lease) as renewer,
        ):
            time.sleep(0.2)
            self.assertTrue(renewer.is_alive())
        self.assertGreaterEqual(lease.renewals, 3)  # tick 1 raised; later ticks renewed
        self.assertEqual(len(logs.output), 1)
        self.assertIn("renewal failed", logs.output[0])

    def test_a_renew_that_keeps_raising_counts_as_lost_after_one_window(self) -> None:
        """An outage longer than the lease window: the key has expired whether or
        not the cache can say so, so the renewer gives up through the same lost
        exit a False return takes (no eternal retry, bounded log noise)."""
        lease = _FakeLease(raise_on=range(1, 1000))
        with (
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
            _renewer(lease) as renewer,
        ):
            time.sleep(0.4)  # the window (0.15s) passes unrenewed after ~7 raising ticks
            self.assertFalse(renewer.is_alive())
        self.assertTrue(6 <= lease.renewals <= 9, lease.renewals)  # gave up at the window, not at 0.4s
        self.assertIn("lease lost", logs.output[-1])
