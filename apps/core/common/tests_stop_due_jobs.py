"""stop_due_jobs: the process-match predicate and the shared job discovery.

The /proc scan + os.kill glue in `handle` is left to manual/integration use (it's
Linux-container-only); the load-bearing logic is the pure `job_command_in` predicate
and `iter_single_flight_commands` discovery, tested here.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from common.commands import iter_single_flight_commands
from common.management.commands.stop_due_jobs import job_command_in

_JOBS = {"process_due_runs", "poll_due_feeds"}


class JobCommandInTests(SimpleTestCase):
    def test_matches_python_manage_py_job_invocation(self) -> None:
        argv = ["/app/.venv/bin/python3", "apps/core/manage.py", "process_due_runs"]
        self.assertEqual(job_command_in(argv, _JOBS), "process_due_runs")

    def test_ignores_uv_wrapper(self) -> None:
        # argv[0] is uv, not python: we want the manage.py run that installed the
        # SIGTERM handler, not the wrapper (which forwards/exits when its child does).
        argv = ["uv", "run", "--package", "openmagpie-core", "python", "apps/core/manage.py", "process_due_runs"]
        self.assertIsNone(job_command_in(argv, _JOBS))

    def test_ignores_non_singleflight_command(self) -> None:
        argv = ["/app/.venv/bin/python3", "apps/core/manage.py", "stop_due_jobs"]
        self.assertIsNone(job_command_in(argv, _JOBS))

    def test_command_name_in_a_flag_value_does_not_match(self) -> None:
        # Only the token right after manage.py counts, so a flag VALUE that echoes a
        # job name can't false-match into a stray SIGTERM.
        argv = ["/app/.venv/bin/python3", "apps/core/manage.py", "some_other", "--note", "process_due_runs"]
        self.assertIsNone(job_command_in(argv, _JOBS))

    def test_empty_argv(self) -> None:
        self.assertIsNone(job_command_in([], _JOBS))


class DiscoverySingleFlightTests(SimpleTestCase):
    def test_discovers_singleflight_commands_not_plain_ones(self) -> None:
        names = {name for name, _ in iter_single_flight_commands()}
        self.assertIn("process_due_runs", names)  # a real SingleFlightCommand
        self.assertNotIn("stop_due_jobs", names)  # plain BaseCommand (the signaler)
        self.assertNotIn("clear_job_locks", names)  # plain BaseCommand
