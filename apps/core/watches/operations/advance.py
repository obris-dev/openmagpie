"""enqueue_next: advance SUCCEEDED run(s) to the next chain action.

Shared by the drain (per-item) and the digest flush (per-batch) so the
instant-vs-digest decision lives in one place. Call INSIDE a transaction:
a digest next opens its window via select_for_update.
"""

from __future__ import annotations

import builtins
from datetime import datetime

from openmagpie_schema.watch_actions import DeliveryConfigBase
from watches.models import WatchAction, WatchActionRun
from watches.registry import load_config
from watches.services import WatchActionRunService, WatchActionService, WatchDigestWindowService


def enqueue_next(run: WatchActionRun, action: WatchAction, *, now: datetime) -> None:
    """Enqueue the run for `action`'s successor in the chain (no-op if
    `action` is the tail). An instant successor is scheduled now ; a digest
    successor joins its digest window (scheduled at the window close)."""
    enqueue_next_batch([run], action, now=now)


def enqueue_next_batch(runs: builtins.list[WatchActionRun], action: WatchAction, *, now: datetime) -> None:
    """Batch variant: advance every run in `runs` (all of the same `action`,
    e.g. a flushed digest batch) to the shared successor. The successor is
    identical for the whole batch, so next_in_chain is resolved ONCE, a digest
    window is opened ONCE, and the successor runs are enqueued in ONE
    bulk insert (enqueue_advance_batch) — no per-run re-query or round trip
    (the N+1 the per-item loop would otherwise incur). No-op on an empty batch
    or a tail action."""
    if not runs:
        return
    account_id = str(runs[0].account_id)
    nxt = WatchActionService(account_id=account_id).next_in_chain(action)
    if nxt is None:
        return
    config = load_config(nxt)
    if isinstance(config, DeliveryConfigBase) and config.is_digest():
        scheduled_at = WatchDigestWindowService(account_id=account_id).open_window(
            str(nxt.id), interval_seconds=config.digest_interval_seconds, now=now
        )
    else:
        scheduled_at = now
    WatchActionRunService(account_id=account_id).enqueue_advance_batch(
        action_id=str(nxt.id),
        kind=str(nxt.kind),
        scheduled_at=scheduled_at,
        rows=[(str(run.watch_id), str(run.feed_item_id), str(run.id)) for run in runs],
    )
