"""Shared run-state constants + the `completed_at` rule for the `runs`
subpackage (drain + service)."""

from datetime import datetime

from django.conf import settings

from openmagpie_schema.watch_enums import CLAIMABLE_STATES, TERMINAL_STATES, WatchActionRunState

_PENDING = WatchActionRunState.PENDING.value
_RUNNING = WatchActionRunState.RUNNING.value
_FAILED = WatchActionRunState.FAILED.value
_SUCCEEDED = WatchActionRunState.SUCCEEDED.value

# Value-string views of the shared enum classification (queries compare the
# bare CharField). _CLAIMABLE: states the drain may re-claim (PENDING +
# retryable FAILED). _TERMINAL: clean final states (FAILED is terminal only
# once exhausted — see completion_ts).
_CLAIMABLE = tuple(s.value for s in CLAIMABLE_STATES)
_TERMINAL = frozenset(s.value for s in TERMINAL_STATES)


def completion_ts(state: str, attempts: int, now: datetime) -> datetime | None:
    """The `completed_at` for a run resting at `state` with `attempts` burned:
    `now` once the run is TERMINAL, else None (still claimable -> will run
    again). ONE rule for every transition site so the field means exactly
    "reached a terminal state" — never a retry-pending failure. FAILED is
    terminal only when attempts are exhausted (the reaper / fail_batch encode
    the same rule in bulk via an attempts partition)."""
    if state in _TERMINAL or (state == _FAILED and attempts >= settings.WATCH_RUN_MAX_ATTEMPTS):
        return now
    return None


# Trigger-enqueue chunk: feed-item ids per SELECT-have + bulk_create round.
# Bounds BOTH the in-memory footprint (the `have` set + row list) AND the
# INSERT size — same unit of work, so ONE constant (splitting invites drift).
# Chunk <= this, so bulk_create needs no own `batch_size`: one chunk = one
# INSERT. A module constant (internal perf knob, no per-deployment meaning).
_ENQUEUE_CHUNK = 500
