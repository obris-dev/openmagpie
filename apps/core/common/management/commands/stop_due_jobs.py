"""Gracefully stop the in-container scheduled-job passes.

`make down-jobs` kills the HOST ticker loops, but a pass already running inside the
container is an orphaned `docker exec` child the host-side kill never reaches, so it
keeps going. Left alone, and with `clear_job_locks` then freeing its lock, the next
`up-jobs` starts a pass CONCURRENT with the orphan; repeated stop/starts pile up
several long-running passes competing for claims and engine rate limit.

This sends SIGTERM to those in-container job processes so each unwinds through
SingleFlightCommand's SystemExit handler: its job_lock releases cleanly and (the
drain) shuts its thread pool down. Targets are discovered from the command registry
(every SingleFlightCommand), not a hardcoded list. Reads /proc, so it is a Linux /
in-container tool; a no-op elsewhere (e.g. a macOS test host).
"""

import os
import signal
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from common.commands import iter_single_flight_commands


def _read_argv(cmdline: Path) -> list[str]:
    """The NUL-separated /proc/<pid>/cmdline as an argv list; [] if unreadable
    (the process exited, or we lack permission) so the caller just skips it."""
    try:
        raw = cmdline.read_bytes()
    except OSError:
        return []
    return [arg.decode("utf-8", "replace") for arg in raw.split(b"\x00") if arg]


def job_command_in(argv: list[str], job_commands: set[str]) -> str | None:
    """The scheduled-job command name if `argv` is a `python ... manage.py <cmd> ...`
    invocation for a known single-flight <cmd>, else None. Matches argv[0] being a
    python (so the uv wrapper and the shell ticker are skipped) and the token right
    AFTER manage.py (so a flag value that merely contains a command name can't
    false-match). The management-command process is the one that installed the
    SIGTERM handler, so it is the one worth signaling."""
    if not argv or "python" not in argv[0].rsplit("/", 1)[-1]:
        return None
    for i, token in enumerate(argv):
        if token.endswith("manage.py") and i + 1 < len(argv):
            candidate = argv[i + 1]
            return candidate if candidate in job_commands else None
    return None


class Command(BaseCommand):
    help = "SIGTERM the in-container scheduled-job processes so each releases its lock and exits gracefully."

    def handle(self, *args: Any, **options: Any) -> None:
        proc = Path("/proc")
        if not proc.is_dir():
            self.stdout.write("no /proc (in-container Linux tool only); nothing to signal")
            return
        job_commands = {name for name, _ in iter_single_flight_commands()}
        me = os.getpid()
        signaled = 0
        for entry in proc.iterdir():
            if not entry.name.isdigit() or int(entry.name) == me:
                continue
            command = job_command_in(_read_argv(entry / "cmdline"), job_commands)
            if command is None:
                continue
            try:
                os.kill(int(entry.name), signal.SIGTERM)
            except (ProcessLookupError, PermissionError) as exc:
                # Raced with the process exiting, or we can't signal it; report and
                # move on. down-jobs still clears locks afterward as the fallback.
                self.stderr.write(f"could not signal pid {entry.name} ({command}): {exc}")
                continue
            signaled += 1
            self.stdout.write(f"SIGTERM -> pid {entry.name} ({command})")
        self.stdout.write(f"signaled {signaled} in-flight job process(es)")
