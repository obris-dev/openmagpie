"""The job lease on the PRODUCTION cache path: `DatabaseCache`, the renewer on
its own thread-local Postgres connection, and that connection terminated
mid-pass. The locmem tests cannot exercise this; it is the exact outage that
used to kill the renewer silently.

A `TransactionTestCase`, not `TestCase`: the renewer's connection is a second
session, and a lock row written inside a test transaction would be invisible to
it (every tick would report the lease lost). Real commits cost a table flush per
test, so this file stays small."""

import time
from unittest import mock

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import TransactionTestCase, override_settings

from common.commands import SingleFlightCommand
from common.locks import job_lock_key

_KEY = job_lock_key("test.leased_db")


class _Leased(SingleFlightCommand):
    job_name = "test.leased_db"


def _terminate_other_sessions() -> int:
    """Kill every other backend on this database (the test runner is one process,
    so the only other session is the renewer thread's). Returns how many."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        return int(cur.fetchone()[0])


@override_settings(JOB_LOCK_LEASE_SECONDS=3, JOB_LOCK_MAX_HOLD_SECONDS=86400)
class JobLeaseDatabaseCacheTests(TransactionTestCase):
    def setUp(self) -> None:
        cache.clear()

    def test_a_terminated_renewer_connection_reconnects_and_keeps_the_lock(self) -> None:
        """Tick 1 (~1s) renews and opens the thread's connection; the main thread
        then terminates that backend; tick 2 (~2s) raises, drops the dead
        connection and is retried; tick 3 (~3s) renews on a fresh one, inside the
        3s window, so the key is still held (same token) at the end of a 5s pass."""
        seen: dict[str, object] = {}

        def body(*_args: object, **_kwargs: object) -> None:
            token = cache.get(_KEY)
            time.sleep(1.5)
            seen["terminated"] = _terminate_other_sessions()
            time.sleep(3.5)
            seen["held_at_end"] = cache.get(_KEY)
            seen["same_token"] = cache.get(_KEY) == token

        with (
            mock.patch.object(BaseCommand, "execute", side_effect=body),
            self.assertLogs("common.lease_renewer", level="WARNING") as logs,
        ):
            _Leased().execute()
        self.assertEqual(seen["terminated"], 1)  # the renewer's session, and only that
        self.assertIsNotNone(seen["held_at_end"])
        self.assertTrue(seen["same_token"])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("renewal failed", logs.output[0])
        self.assertIn("OperationalError", logs.output[0])  # exc_info carried the cause
        self.assertIsNone(cache.get(_KEY))  # released on exit
