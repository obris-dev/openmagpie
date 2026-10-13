"""The job lock as a renewed lease: `SingleFlightCommand` keeps its lock alive
for the life of the pass and lets it lapse when the pass dies.

Split from `tests.py` (near the length cap). Timings here are real seconds
against the locmem cache: the TTL is set to a few seconds and the pass body
sleeps past it, so a test that passes proves the renewer, not the TTL, held
the lock. The margins are deliberately wide (a renewal every third of the
window) so a slow CI box doesn't flake them.
"""

import threading
import time
from unittest import mock

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.test import SimpleTestCase, override_settings

from common.commands import SingleFlightCommand
from common.lease_renewer import RENEWER_THREAD_PREFIX
from common.locks import job_lock, job_lock_key

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "job-lease-tests"}}
_KEY = job_lock_key("test.leased")


class _Leased(SingleFlightCommand):
    job_name = "test.leased"


def _renewer_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith(RENEWER_THREAD_PREFIX)]


@override_settings(CACHES=_LOCMEM, JOB_LOCK_LEASE_SECONDS=3, JOB_LOCK_MAX_HOLD_SECONDS=86400)
class JobLeaseRenewalTests(SimpleTestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_lock_outlives_its_ttl_while_the_pass_runs(self) -> None:
        """The positive case: with a 3s TTL a pass that runs 5s would lose the
        lock at 3s under a fixed TTL. The renewer re-stamps it every second,
        so a second launch mid-pass still skips, and the key is still held
        (same token) right up to the end of the body."""
        seen: dict[str, object] = {}

        def body(*_args: object, **_kwargs: object) -> None:
            token = cache.get(_KEY)
            time.sleep(5)
            seen["held_at_end"] = cache.get(_KEY)
            seen["same_token"] = cache.get(_KEY) == token
            # A rival launch during the pass is single-flighted, as before.
            out = _Leased()
            with mock.patch.object(BaseCommand, "execute") as rival:
                out.execute()
            seen["rival_ran"] = rival.called

        with mock.patch.object(BaseCommand, "execute", side_effect=body):
            _Leased().execute()
        self.assertIsNotNone(seen["held_at_end"])
        self.assertTrue(seen["same_token"])
        self.assertFalse(seen["rival_ran"])
        self.assertIsNone(cache.get(_KEY))  # released on exit, as before

    def test_renewer_stops_when_the_pass_ends_normally_or_raises(self) -> None:
        """No thread outlives its pass (a leak would keep renewing a released
        key's name, re-creating the lock after release)."""
        with mock.patch.object(BaseCommand, "execute", return_value=None):
            _Leased().execute()
        self.assertEqual(_renewer_threads(), [])
        with (
            mock.patch.object(BaseCommand, "execute", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            _Leased().execute()
        self.assertEqual(_renewer_threads(), [])
        self.assertIsNone(cache.get(_KEY))

    def test_a_dead_holder_frees_after_one_window(self) -> None:
        """The crash failsafe: a holder that stops renewing (SIGKILL, OOM, its
        container recreated) loses the key after the lease window, so the next
        pass acquires instead of skipping. Taken through `job_lock` itself (so
        the TTL under test is JOB_LOCK_LEASE_SECONDS, not a hand-seeded one) and
        abandoned without unwinding, exactly what a hard kill does. The holder
        is kept referenced: a garbage-collected generator context manager would
        run its finally and release the key, which is the opposite of a kill."""
        holder = job_lock("test.leased")
        self.assertTrue(holder.__enter__())  # acquired via the real path; never __exit__'d
        with mock.patch.object(BaseCommand, "execute") as body:
            _Leased().execute()
            self.assertFalse(body.called)  # still held: skipped
        time.sleep(3.5)
        with mock.patch.object(BaseCommand, "execute") as body:
            _Leased().execute()
            self.assertTrue(body.called)  # lapsed: the new pass runs

    def test_a_lost_lease_is_logged_and_the_pass_continues(self) -> None:
        """If the key is cleared under a live pass (`clear_job_locks` by hand,
        or a cache outage longer than the window) the renewer reports it once
        and stops; the pass itself is not interrupted, because single-flight is
        a convenience, not a correctness bound."""
        done: dict[str, bool] = {}

        def body(*_args: object, **_kwargs: object) -> None:
            cache.set(_KEY, "someone-else", timeout=60)
            time.sleep(2.5)  # past at least one renewal tick
            done["finished"] = True

        with (
            mock.patch.object(BaseCommand, "execute", side_effect=body),
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
        ):
            _Leased().execute()
        self.assertTrue(done["finished"])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("lease lost", logs.output[0])
        self.assertEqual(cache.get(_KEY), "someone-else")  # the new owner's key is left alone

    @override_settings(JOB_LOCK_MAX_HOLD_SECONDS=2)
    def test_renewer_gives_up_past_the_max_hold(self) -> None:
        """The hung-pass backstop: a pass alive past JOB_LOCK_MAX_HOLD_SECONDS
        stops being renewed, so its 3s lease lapses while it is still running
        and the next pass can take over. (A real pass is far shorter than the
        day-long default; 2s here just makes the cap bite inside the test.)"""
        seen: dict[str, object] = {}

        def body(*_args: object, **_kwargs: object) -> None:
            time.sleep(6)  # cap at 2s, then one more lease window of 3s to lapse
            seen["held_at_end"] = cache.get(_KEY)

        with (
            mock.patch.object(BaseCommand, "execute", side_effect=body),
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
        ):
            _Leased().execute()
        self.assertIsNone(seen["held_at_end"])  # lapsed under the still-running pass
        self.assertEqual(len(logs.output), 1)
        self.assertIn("max hold", logs.output[0])
