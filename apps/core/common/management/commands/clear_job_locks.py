"""Clear orphaned single-flight job locks.

A `job_lock` is freed by its owner on any graceful exit, including SIGTERM
WHEN it reaches the run (SingleFlightCommand turns it into a SystemExit). It
is NOT freed when SIGTERM never reaches the run (e.g. `make down-jobs` kills
the host-side ticker wrapper, not the in-container manage.py) or on a hard
SIGKILL / OOM / power loss; those orphan the lock until its day-long TTL.
This command is the manual release for those cases: delete the `job_lock:*`
cache key so the next pass can acquire immediately.

WARNING: the lock lives in the SHARED cache, so it is cluster-wide. Clearing
a key here frees it for every machine, including one where a job is still
genuinely running. Only run this once the relevant jobs are stopped (this is
why `make down-jobs` wires it in: local, after the tickers are down).
"""

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError, CommandParser

from common.commands import iter_single_flight_commands
from common.locks import job_lock_key

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Delete orphaned single-flight job locks (after a hard kill). Pass --job <name> or --all."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--job",
            action="append",
            dest="jobs",
            default=[],
            metavar="NAME",
            help="Job name to clear (the <app>.<command> from `resolve_job_name`); repeatable.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Clear locks for every discovered SingleFlightCommand job.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report which job locks are currently held; delete nothing. (Used by `make up-jobs`.)",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm a cluster-wide --all clear when DJANGO_ENV=cloud (the shared cache makes it high blast-radius).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        requested: list[str] = list(options["jobs"])
        discovered = self._discover_jobs()
        jobs: list[str] = list(requested)
        if options["all"]:
            # --all on a SHARED (cloud) cache frees every job lock cluster-wide,
            # including ones a live run on another machine holds. A targeted
            # --job <name> stays precise, so gate only the blanket clear, and
            # only in cloud: require an explicit --yes (non-interactive, so it
            # still scripts / wires into make down-jobs locally).
            if settings.IS_CLOUD and not options["dry_run"] and not options["yes"]:
                raise CommandError(
                    "--all clears job locks cluster-wide on the shared cache. Re-run with --yes "
                    "to confirm, or target one lock with --job <name>."
                )
            jobs = sorted(set(jobs) | discovered)
        if not jobs:
            raise CommandError("nothing to clear: pass --job <name> (repeatable) or --all")

        # Typo guard: a --job name that isn't a known single-flight job would
        # otherwise just print "was not held" and exit 0, hiding the mistake.
        # Warn, but still clear it: it could be a genuine orphan from a removed
        # or renamed command whose class no longer resolves.
        for name in requested:
            if name not in discovered:
                self.stderr.write(f"warning: {name!r} is not a known single-flight job (typo?); clearing it anyway")

        held = [name for name in jobs if cache.get(job_lock_key(name)) is not None]

        if options["dry_run"]:
            if held:
                self.stdout.write("job locks currently HELD: " + ", ".join(held))
                self.stdout.write(
                    "If a run is genuinely in flight this is expected. If nothing is running, "
                    "they are orphaned (a hard kill skips the release) and every pass will skip "
                    "until you clear them (`make down-jobs`, or `clear_job_locks --all`) or the "
                    "TTL expires."
                )
            else:
                self.stdout.write("no job locks currently held")
            return

        self.stderr.write(
            "WARNING: job locks are cluster-wide (shared cache). Only clear them once the "
            "jobs are stopped, or you may free a lock a live run elsewhere still holds."
        )
        for name in jobs:
            key = job_lock_key(name)
            cache.delete(key)
            self.stdout.write(f"cleared {key}" if name in held else f"{key} was not held")

    @staticmethod
    def _discover_jobs() -> set[str]:
        """Every SingleFlightCommand's lock name, via the shared registry walk, so
        --all needs no hardcoded list and can't drift as jobs are added or renamed.
        resolve_job_name() is guarded per command: a misconfigured one that raises
        must not strand the whole sweep (this is the incident-response path), so we
        skip it but log the skip so a missed lock stays traceable."""
        names: set[str] = set()
        for command_name, command in iter_single_flight_commands():
            try:
                names.add(command.resolve_job_name())
            except Exception as exc:
                logger.warning("clear_job_locks: skipping %r; could not resolve its job lock: %s", command_name, exc)
        return names
