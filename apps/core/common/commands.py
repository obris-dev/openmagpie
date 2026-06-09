"""SingleFlightCommand: a management command that won't run twice at once.

Scheduled jobs (poll / trigger / drain) are launched on a cadence, and a
pass can outlast its interval (a deep drain backlog judges items
synchronously). Subclassing this makes a second launch of the SAME command
log and skip while one is in flight, instead of piling up behind it. The
lock key defaults to the command's own file name (its invocation name), so
each command single-flights against itself, never against the others.

Built on `job_lock` (cache/DB-backed via `named_lock`), so it serializes
across processes AND machines, and releases via the lock's finally on a
normal exit or exception. `execute` also installs a handler that turns
SIGTERM into a SystemExit so the finally still runs WHEN the signal
reaches this process (a k8s pod stop, a direct `kill` of the run). When
SIGTERM does NOT reach it (e.g. `make down-jobs` kills the host-side
ticker wrapper, not the in-container run) or on a hard SIGKILL / power
loss, the handler can't fire; `clear_job_locks` and the day-long TTL are
the release path there. Opt-in: a command that WANTS to run N-wide (the
drain is safe to, via its CAS claim) just stays on plain BaseCommand.
"""

import logging
import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from common.locks import job_lock

logger = logging.getLogger(__name__)

# The marker every management-command module path contains. The default
# lock name is the segment AFTER it (the file = the invocation name).
_COMMANDS_MARKER = ".management.commands."


@contextmanager
def _sigterm_as_systemexit() -> Iterator[None]:
    """While active, translate SIGTERM into a SystemExit so `with` finally
    blocks run on a graceful stop.

    Python's default SIGTERM action terminates the process WITHOUT unwinding
    the stack, so `finally` (and thus the job_lock release) never runs and
    the lock orphans until its TTL. Raising SystemExit instead lets the
    stack unwind normally. Only installs in the main thread (signal.signal
    requires it); restores the prior handler on exit. SIGKILL / power loss
    stay uncatchable, so the TTL remains the failsafe for those."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, _raise)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


class SingleFlightCommand(BaseCommand):
    """BaseCommand that holds `job_lock(<job name>)` for the duration of a run.

    The job name defaults to the command's APP-QUALIFIED name,
    `<app>.<command>`, both pulled from the
    `<app>.management.commands.<command>` module path, so each command
    single-flights against itself, and two apps that happen to ship a
    same-named command don't collide on one lock. Set the `job_name` class
    attribute to override it (e.g. to share one lock across commands)."""

    # None -> derive `<app>.<command>` from the module (see resolve_job_name).
    job_name: ClassVar[str | None] = None

    def resolve_job_name(self) -> str:
        """The lock key for this command: the explicit `job_name` override,
        else the app-qualified `<app>.<command>` derived from the module.

        Validates the derived case: the default only makes sense for a real
        `management/commands/<name>.py` module, so if this class lives
        anywhere else (and gave no `job_name`) we'd be locking on a
        misleading key ; fail loud and point at the fix instead."""
        if self.job_name:
            return self.job_name
        module = type(self).__module__
        app, sep, command = module.partition(_COMMANDS_MARKER)
        if not sep:
            raise ImproperlyConfigured(
                f"{type(self).__name__}.__module__={module!r} is not a management-command module; "
                "set a `job_name` class attribute to name its single-flight lock explicitly."
            )
        return f"{app}.{command}"

    def execute(self, *args: Any, **options: Any) -> Any:
        name = self.resolve_job_name()
        # SIGTERM handler OUTSIDE the lock: on a graceful stop the SystemExit
        # unwinds through job_lock's finally (release) first, then restores
        # the prior handler.
        with _sigterm_as_systemexit(), job_lock(name) as acquired:
            if not acquired:
                logger.info("job %s already running; skipping this pass", name)
                self.stdout.write(f"{name} already running; skipping")
                return None
            return super().execute(*args, **options)
