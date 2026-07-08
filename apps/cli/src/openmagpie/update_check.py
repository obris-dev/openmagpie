"""Ambient once-a-day "a newer magpie is on PyPI" nudge.

Runs on close of every command (wired into cli.py's app callback via `call_on_close`),
so it never delays the command's own output. Deliberately quiet: it makes NO network
call and prints NOTHING while the cached check is still fresh (< _TTL). Only when the
cache is stale does it refresh (a short-timeout PyPI lookup, failures swallowed) and,
if this CLI is behind, print a single stderr line pointing at `magpie upgrade`. So the
nudge appears at most once a day, on the one command that happens to refresh.

Suppressed entirely when stdout isn't a TTY (scripts / pipes never see it, and it can't
corrupt --jsonl / -o output), when MAGPIE_NO_UPDATE_CHECK is set, and for the version /
upgrade commands (which surface the same information themselves).

An app-lifecycle hook, not a command, so it sits at the package top level (like
console/context/config) and reasons about versions via the shared `versions` module."""

from __future__ import annotations

import contextlib
import os
import sys
from datetime import UTC, datetime, timedelta

from . import __version__, console
from .config import UpdateCheck, load_update_check, save_update_check
from .versions import is_behind, latest_version

_TTL = timedelta(hours=24)  # hit PyPI at most once a day
_LOOKUP_TIMEOUT = 2.0  # short: off the command's critical path, but still bounded
# Opt-outs: our own switch, plus the cross-tool DO_NOT_TRACK the telemetry system
# already honors (this nudge is a second outbound signal, so it respects the same knob).
_OPT_OUT_ENV = "MAGPIE_NO_UPDATE_CHECK"
_DNT_ENV = "DO_NOT_TRACK"
_SKIP_COMMANDS = frozenset({"version", "upgrade"})  # these surface the nudge themselves


def maybe_nudge(invoked_command: str | None) -> None:
    """Print the update-available nudge if warranted. See the module docstring for the
    quiet-by-default contract and the suppression rules."""
    if invoked_command is None or invoked_command in _SKIP_COMMANDS:
        return
    if os.environ.get(_OPT_OUT_ENV) or os.environ.get(_DNT_ENV):
        return
    if not sys.stdout.isatty():  # scripted / piped: stay silent, keep output clean
        return

    behind = is_behind(__version__, _refresh_if_due())  # shared with version/upgrade
    if behind:
        console.hint(f"A newer magpie is available: {__version__} -> {behind}. Run `magpie upgrade`.")


def record(latest: str | None) -> None:
    """Stamp the cache from a lookup another command already made (`magpie version` /
    `magpie upgrade` both fetch the PyPI latest). Resets the once-a-day timer so the
    ambient nudge doesn't re-hit PyPI on the very next command, and doesn't re-nudge
    right after the user just saw the version info. No-op on a failed lookup (None) so
    we only ever cache a real result."""
    if latest is None:
        return
    _safe_save(UpdateCheck(last_checked_at=datetime.now(UTC), latest=latest))


def _refresh_if_due() -> str | None:
    """If the cached check is stale (or absent), refresh it and return the latest
    version to consider nudging on; if it's still fresh, return None so we stay silent
    with no network call.

    The timestamp advances on EVERY refresh attempt, success or not, so an offline run
    doesn't re-hit the network (and re-pay the timeout) on every later command; on
    failure we carry the previously cached `latest` forward."""
    now = datetime.now(UTC)
    cached = _safe_load()
    if cached is not None and (now - cached.last_checked_at) < _TTL:
        return None

    latest = latest_version(timeout=_LOOKUP_TIMEOUT)
    resolved = latest or (cached.latest if cached else None)
    _safe_save(UpdateCheck(last_checked_at=now, latest=resolved))
    return resolved


def _safe_load() -> UpdateCheck | None:
    """Load the cache, treating any failure as "no cache" (-> refresh). A cosmetic
    nudge must never break a command; `load` raises RuntimeError on a corrupt file."""
    try:
        return load_update_check()
    except (OSError, ValueError, RuntimeError):
        return None


def _safe_save(update_check: UpdateCheck) -> None:
    """Persist the cache, swallowing write failures (read-only home, race, corrupt
    file): worst case we simply check again next command. Never surfaced to the user."""
    with contextlib.suppress(OSError, ValueError, RuntimeError):
        save_update_check(update_check)
