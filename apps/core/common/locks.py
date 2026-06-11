"""Cache-backed try-locks.

`named_lock(name, timeout)` is the general primitive: a non-blocking
mutex keyed by an opaque name. Built on `cache.add` (atomic). Yields a
`LockLease` (truthy iff acquired); caller decides whether to skip, retry,
or 409.

`timeout` is a LIVENESS lease, not a total-work budget: a holder that
expects to run longer than `timeout` calls `lease.renew()` as it makes
progress to re-stamp the TTL, so the lock survives a long-but-live run
while a crashed holder (no more renewals) still frees after one window.
This is the etcd-lease / Kubernetes-Lease pattern. We do NOT issue fencing
tokens: on these paths the only cost of a brief overlap is redundant,
idempotent work (re-polling a source, re-pruning), never corruption.

The feed poll wrapper (`poll_lock`), the feed set-sources wrapper
(`feed_set_lock`), and the refresh-rotation wrapper (`refresh_token_lock`)
are thin shims that just pick the cache key and timeout for their scope.
"""

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

from django.conf import settings
from django.core.cache import cache


class LockLease:
    """A held (or missed) `named_lock`. Truthy iff acquired, so `if not
    lease:` reads naturally for callers that only skip/retry.

    `renew()` extends the lease while we still own the key, turning the
    fixed `timeout` into a liveness window: renew as you work and the lock
    outlives any run; stop renewing (crash) and it frees after one window."""

    def __init__(self, *, name: str, token: str, acquired: bool, timeout: int) -> None:
        self._name = name
        self._token = token
        self._timeout = timeout
        self.acquired = acquired

    def __bool__(self) -> bool:
        return self.acquired

    def renew(self) -> bool:
        """Re-stamp the lease TTL iff we still hold it. Returns whether the
        lease is still ours: False means it expired and another holder took
        over (the caller should stop). Same owner-token TOCTOU tolerance as
        release: a momentary overlap is possible but harmless where overlap
        only costs redundant idempotent work."""
        if not self.acquired:
            return False
        if cache.get(self._name) != self._token:
            self.acquired = False  # expired under us; someone else owns it now
            return False
        cache.set(self._name, self._token, timeout=self._timeout)
        return True


@contextmanager
def named_lock(*, name: str, timeout: int) -> Iterator[LockLease]:
    """Try-lock keyed by `name`. Yields a `LockLease` (truthy iff acquired).

    On release, only deletes the cache key if we're still the owner,
    guards against the case where our work outran `timeout`, the key
    auto-expired, another process re-acquired with a fresh token, and
    our stale `cache.delete` would otherwise clobber theirs. (A holder that
    renews as it works avoids that overrun entirely.)
    """
    token = uuid.uuid4().hex
    acquired = bool(cache.add(name, token, timeout=timeout))
    try:
        yield LockLease(name=name, token=token, acquired=acquired, timeout=timeout)
    finally:
        if acquired and cache.get(name) == token:
            cache.delete(name)


def poll_lock(feed_id: str) -> AbstractContextManager[LockLease]:
    """Lock a feed's poll cycle. Yields a `LockLease`; the poll renews it
    per source so a feed of any size polls under one continuously-held lock
    (`POLL_LOCK_TIMEOUT_SECONDS` is the inter-source liveness window, not a
    cap on total poll time)."""
    return named_lock(
        name=f"feed_poll_lock:{feed_id}",
        timeout=settings.POLL_LOCK_TIMEOUT_SECONDS,
    )


def feed_set_lock(feed_id: str) -> AbstractContextManager[LockLease]:
    """Serialize concurrent `SourceService.set_sources` on one feed.

    Two operators racing `magpie feed source set` on the same feed
    each snapshot the existing rows independently and compute
    `removed = existing - desired`. The loser's removed set can drop
    rows the winner just added or kept, and both report success. The
    lock collapses the race to a single ordered apply; the loser
    sees a clean retry-friendly error."""
    return named_lock(
        name=f"feed_set_lock:{feed_id}",
        timeout=settings.FEED_SET_LOCK_TIMEOUT_SECONDS,
    )


def path_chain_lock(path_id: str) -> AbstractContextManager[LockLease]:
    """Serialize chain mutations on one WatchPath (add / remove / replace).

    Rank uniqueness is per-path (`unique(account_id, path_id, rank)`), so
    the path is the right grain. Two concurrent add/removes each snapshot
    the chain, recompute dense ranks, and write back ; without serializing
    they collide on the rank constraint (or interleave into a gapped
    chain). Locking the PATH (not its action rows) also covers the
    add-first-action race, where a row-level lock would find nothing to
    lock. The loser gets a clean retry-friendly error."""
    return named_lock(
        name=f"path_chain_lock:{path_id}",
        timeout=settings.PATH_CHAIN_LOCK_TIMEOUT_SECONDS,
    )


# Cache-key prefix for `job_lock`. Shared with `clear_job_locks` so the ops
# command deletes the exact key `job_lock` writes (one source of truth).
JOB_LOCK_KEY_PREFIX = "job_lock:"


def job_lock_key(name: str) -> str:
    """The cache key `job_lock(name)` writes. Use this to clear it."""
    return f"{JOB_LOCK_KEY_PREFIX}{name}"


def job_lock(name: str) -> AbstractContextManager[LockLease]:
    """Single-flight a scheduled job (a management command) by name.

    Skip-if-held: yields True iff acquired ; a run that finds a prior pass
    still going gets False and should log + skip rather than pile up behind
    it. Built on `named_lock`, so release rides the finally on a normal exit
    or a handled exception, and on SIGTERM too WHEN it reaches the process
    that installed the handler (SingleFlightCommand does). When SIGTERM
    never reaches that process (a supervisor kills a wrapper, e.g. `make
    down-jobs`) or on a raw SIGKILL / power loss, the finally can't run;
    `clear_job_locks` is the manual release for those, and
    `JOB_LOCK_TIMEOUT_SECONDS` (deliberately a full day, so a legitimately
    hours-long pass never expires) is the eventual failsafe."""
    return named_lock(
        name=job_lock_key(name),
        timeout=settings.JOB_LOCK_TIMEOUT_SECONDS,
    )


def refresh_token_lock(refresh_token: str) -> AbstractContextManager[LockLease]:
    """Serialize concurrent refresh-token rotations for a single token.

    Hashed so the raw token value never appears as a cache key (db cache
    rows are visible to anyone with table access). Short failsafe TTL
    since the rotation critical section is a single read + revoke +
    mint, measured in milliseconds.
    """
    digest = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()[:32]
    return named_lock(
        name=f"refresh_token_lock:{digest}",
        timeout=settings.REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS,
    )
