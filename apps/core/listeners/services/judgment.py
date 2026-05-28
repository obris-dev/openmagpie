"""Judgment orchestrator: a Listener judges new FeedItems with its engine.

The Feed polls and persists every item; the Listener is an attention over
that Feed. This drives the listener leg: read FeedItems the listener
hasn't judged yet (id > its cursor) across every stream in the Feed,
judge each with the engine, and on a hit persist an Event (kind="hit")
and (for instant-mode listeners) fire the notifier.

A per-listener cursor (`Listener.last_judged_item_id`, a ULID) is what
keeps misses from being re-judged: items are processed in id order and the
cursor advances to the snapshot max each cycle. Judgment has no cadence of
its own - it rides the Feed's poll cadence (new items appear when the Feed
polls); a cycle with no new items is a cheap cursor query, no LLM calls.

Each `judge_listener` cycle starts with a stuck-pending retry for
instant-mode listeners (re-fire delivery for any hit left undelivered by a
prior failed cycle). Per-item failures are isolated so one bad payload or
a transient engine/webhook error can't abort the whole cycle.

`JudgeListenerOperation` is a one-shot operation object; build with a
Listener and call `.run()` once.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property

import httpx
from pydantic import ValidationError

from common.locks import poll_lock
from engine import registry as engine_registry
from engine.engines import Engine
from engine.engines.base import JudgeRequest, JudgmentResult
from events.observations import Observation
from events.registry import UnhydrateableObservation, hydrate_data
from events.registry import hydrate as hydrate_event
from events.services import EventKind, EventService
from feeds.models import Feed, FeedItem
from feeds.services import FeedService
from listeners import registry as listeners_registry
from listeners.models import Listener
from notifications.services import DeliveryService

from .listeners import ListenerService

logger = logging.getLogger("listeners")

# Operational failures we expect and recover from. Anything outside this set
# is a programming bug and should propagate. (No connector calls here - the
# Feed does the fetching - so the set is hydrate/judge/deliver failures.)
_RECOVERABLE_ERRORS = (
    httpx.HTTPError,
    ValidationError,
    ConnectionError,
)


@dataclass(frozen=True)
class JudgeResult:
    judged: int
    hits: int


@dataclass(frozen=True)
class JudgeCycleStarted:
    """Fired once at the top of a cycle that has work to do, AFTER the
    cursor/latest snapshot is taken. `pending` is the exact item count
    the loop will iterate. `est_seconds` is `pending * (per-listener
    EWMA of recent judge latency)`, so a cycle on a slow model or busy
    host shows a realistic ETA rather than the seed-default 2s/item.
    Not fired when the snapshot has no new items; that's the cheap
    empty-cycle path."""

    listener: Listener
    pending: int
    est_seconds: int


@dataclass(frozen=True)
class JudgeItemDone:
    """Per-item progress signal. The engine is the slow leg (multi-second
    LLM call per item), so callers wanting live feedback wire an
    `on_progress` callback. Fires on success AND on per-item failures
    (un-hydrateable observation, recoverable engine/connector error)
    so the operator sees errors in the live console, not just in logs.

    `error` is set on failures (with `obs` None for an un-hydrateable
    item, populated otherwise); `score`/`hit` are None on errors.
    `external_id` is the FeedItem's denormalized source id, populated
    on every event so the error path has a render-able identity even
    when `obs` couldn't be hydrated.

    `latency_ms` is the engine's measured judge time for THIS item
    (0 on error). `done` / `total` / `eta_seconds` are the running
    cycle stats AFTER this item, with `eta_seconds` computed from the
    in-cycle mean latency (more accurate than the cross-cycle EWMA
    once a couple items have actually landed)."""

    listener: Listener
    external_id: str = ""
    obs: Observation | None = None
    score: float | None = None
    hit: bool = False
    error: str | None = None
    latency_ms: int = 0
    done: int = 0
    total: int = 0
    eta_seconds: int = 0


JudgeEvent = JudgeCycleStarted | JudgeItemDone
JudgeProgressCallback = Callable[[JudgeEvent], None]


@dataclass(frozen=True)
class _ItemOutcome:
    """Internal carrier from `_judge_item` to `run()`. The orchestrator
    needs the engine result to update running stats before emitting the
    `JudgeItemDone` event, so the per-item method returns data instead
    of emitting directly."""

    obs: Observation
    score: float
    hit: bool
    latency_ms: int


# Per-listener EWMA of judge latency (seconds). Process-local in-memory
# state: lost on container restart, converges within a few items on the
# next cycle. Avoids a schema migration for what's a UI nicety. The seed
# default is on the low side of typical local-Ollama 7B latency so the
# first cycle's ETA looks honest (and the EWMA quickly outgrows it once
# real numbers land).
_LATENCY_EWMA_ALPHA = 0.3
_LATENCY_SEED_SECONDS = 2.0
_listener_latency_ewma: dict[str, float] = {}


def _est_seconds_per_item(listener: Listener) -> float:
    """Per-listener mean of recent judge latencies (EWMA), or the seed
    default when no history exists yet (fresh process / first cycle)."""
    return _listener_latency_ewma.get(str(listener.id), _LATENCY_SEED_SECONDS)


def _record_judge_latency(listener: Listener, latency_ms: int) -> None:
    """Fold one observed latency into the listener's EWMA."""
    key = str(listener.id)
    seconds = latency_ms / 1000.0
    prev = _listener_latency_ewma.get(key)
    _listener_latency_ewma[key] = (
        seconds if prev is None else _LATENCY_EWMA_ALPHA * seconds + (1 - _LATENCY_EWMA_ALPHA) * prev
    )


def _running_eta_seconds(
    pending: int,
    processed: int,
    judged: int,
    cycle_latency_ms: int,
    listener: Listener,
    concurrency: int = 1,
) -> int:
    """Wall-clock ETA in seconds for the rest of the current cycle.

    Uses the in-cycle mean per successful judge when we have data
    (`judged > 0`); falls back to the listener's cross-cycle EWMA for
    the all-errors-so-far edge case. With `concurrency=N`, N items
    finish every `mean_seconds`, so wall-clock = `remaining * mean / N`.
    `remaining = pending - processed` so error items don't inflate it.
    """
    remaining = max(0, pending - processed)
    if remaining == 0:
        return 0
    mean_seconds = (cycle_latency_ms / judged / 1000.0) if judged > 0 else _est_seconds_per_item(listener)
    return max(0, round(remaining * mean_seconds / max(1, concurrency)))


class JudgeListenerOperation:
    """One-shot: judge a single Listener's new FeedItems, persist, deliver."""

    def __init__(self, listener: Listener, *, on_progress: JudgeProgressCallback | None = None) -> None:
        self.listener = listener
        self.config = listeners_registry.load_semantic_config(listener)
        self.account_id = str(listener.account_id)
        self.is_instant = listener.delivery_mode == Listener.DeliveryMode.INSTANT
        self.on_progress: JudgeProgressCallback = on_progress or (lambda _: None)

    @cached_property
    def listener_svc(self) -> ListenerService:
        return ListenerService(account_id=self.account_id)

    @cached_property
    def feed_svc(self) -> FeedService:
        return FeedService(account_id=self.account_id)

    @cached_property
    def event_svc(self) -> EventService:
        return EventService(account_id=self.account_id)

    @cached_property
    def delivery_svc(self) -> DeliveryService:
        return DeliveryService(account_id=self.account_id)

    @cached_property
    def engine(self) -> Engine:
        return engine_registry.get(self.config.engine.kind)

    def run(self) -> JudgeResult:
        """Judge new FeedItems for this listener."""
        if self.is_instant and self.config.notifiers:
            self._retry_stuck_pending()

        try:
            feed = self.feed_svc.get(self.config.feed_id)
        except Feed.DoesNotExist:
            logger.warning(
                "listener %s references missing feed %s; skipping",
                self.listener.id,
                self.config.feed_id,
            )
            return JudgeResult(judged=0, hits=0)

        cursor = self.listener.last_judged_item_id or ""

        # Snapshot the newest item id NOW so items arriving mid-cycle are
        # picked up next time. On a clean cycle we advance the cursor to it.
        # On a RECOVERABLE failure we hold the cursor at the last success
        # instead, so the failed item and everything after it retry next
        # cycle rather than being skipped. FeedItems are persisted, so retry
        # is possible (the old in-memory pipeline couldn't). Trade-off: a
        # permanently-failing item blocks progress past it (loud + rare);
        # bounded retry is a follow-up.
        latest = self.feed_svc.newest_item_id(feed)
        if latest is None or latest <= cursor:
            return JudgeResult(judged=0, hits=0)

        # Size up the cycle before the slow leg. One cheap COUNT against
        # the same `(cursor, latest]` window the loop will iterate, so
        # callers can render "judging N items (~Ns)" up front. ETA uses
        # the per-listener EWMA divided by engine concurrency so the
        # wall-clock estimate accounts for parallel fan-out.
        pending = self.feed_svc.count_items_in_window(feed, after_id=cursor, through_id=latest)
        concurrency = self.engine.concurrency
        if pending > 0:
            est_seconds = max(1, round(pending * _est_seconds_per_item(self.listener) / max(1, concurrency)))
            self.on_progress(JudgeCycleStarted(listener=self.listener, pending=pending, est_seconds=est_seconds))

        judged = 0
        hits = 0
        processed = 0  # success-or-error count; drives the progress display
        last_success = cursor
        failed = False
        # Running in-cycle latency. Used to refine the ETA off the actual
        # mean for THIS cycle (more accurate than the cross-cycle EWMA
        # once any data lands; accounts for model warm-up, host load, etc).
        cycle_latency_ms = 0
        items_iter = self.feed_svc.iter_items_in_window(feed, after_id=cursor, through_id=latest)

        # Batch loop: collect up to `concurrency` hydrated items, submit
        # to `engine.judge_batch`, process results in submission order.
        # Unhydrateable items are emitted as error events and skipped
        # before they reach the batch (cursor advances past them, same
        # as the sequential path used to do).
        while not failed:
            batch: list[tuple[FeedItem, Observation]] = []
            for _ in range(max(1, concurrency)):
                try:
                    item = next(items_iter)
                except StopIteration:
                    break
                try:
                    obs = hydrate_data(item.data)
                except UnhydrateableObservation as exc:
                    # PERMANENT skip: a renamed/removed connector can't
                    # ever hydrate this item. Advance past it so we
                    # don't loop on the same poison row forever; surface
                    # it on-screen so the operator sees the dead row
                    # without grepping logs.
                    logger.warning(
                        "skipping un-hydrateable item listener=%s feed_item=%s: %s",
                        self.listener.id,
                        item.id,
                        exc,
                    )
                    processed += 1
                    last_success = str(item.id)
                    self.on_progress(
                        JudgeItemDone(
                            listener=self.listener,
                            external_id=item.external_id,
                            obs=None,
                            error=f"un-hydrateable: {exc}",
                            done=processed,
                            total=pending,
                            eta_seconds=_running_eta_seconds(
                                pending, processed, judged, cycle_latency_ms, self.listener, concurrency
                            ),
                        )
                    )
                    continue
                batch.append((item, obs))

            if not batch:
                break

            # Fan the batch out to the engine. judge_batch returns one
            # entry per input in submission order; failures come back as
            # exception instances (asyncio.gather(return_exceptions=True)
            # semantics) so a single bad item doesn't poison the rest of
            # the batch.
            requests = [
                JudgeRequest(observation=obs, listener=self.listener, model=self.config.engine.model or None)
                for (_, obs) in batch
            ]
            results = self.engine.judge_batch(requests)

            # Process results in submission order. Cursor advances only
            # through items that PRECEDE any in-batch failure; successes
            # AFTER a failure still emit progress (the LLM cost was
            # paid, the hit is persisted) but don't advance the cursor,
            # so retry next cycle re-judges from the failure point.
            seen_failure_in_batch = False
            for (item, obs), result in zip(batch, results, strict=True):
                if isinstance(result, Exception):
                    if not isinstance(result, _RECOVERABLE_ERRORS):
                        # Programming bug; propagate.
                        raise result
                    logger.warning(
                        "item judgment failed listener=%s feed_item=%s err=%s: %s; "
                        "holding cursor, will retry from here next cycle",
                        self.listener.id,
                        item.id,
                        type(result).__name__,
                        result,
                    )
                    processed += 1
                    self.on_progress(
                        JudgeItemDone(
                            listener=self.listener,
                            external_id=item.external_id,
                            obs=obs,
                            error=f"{type(result).__name__}: {result}",
                            done=processed,
                            total=pending,
                            eta_seconds=_running_eta_seconds(
                                pending, processed, judged, cycle_latency_ms, self.listener, concurrency
                            ),
                        )
                    )
                    seen_failure_in_batch = True
                    failed = True
                    continue

                # Success: persist hit if applicable, deliver if instant.
                outcome = self._persist_and_deliver(item, obs, result)
                if outcome.hit:
                    hits += 1
                judged += 1
                processed += 1
                cycle_latency_ms += outcome.latency_ms
                _record_judge_latency(self.listener, outcome.latency_ms)
                if not seen_failure_in_batch:
                    last_success = str(item.id)
                self.on_progress(
                    JudgeItemDone(
                        listener=self.listener,
                        external_id=item.external_id,
                        obs=outcome.obs,
                        score=outcome.score,
                        hit=outcome.hit,
                        latency_ms=outcome.latency_ms,
                        done=processed,
                        total=pending,
                        eta_seconds=_running_eta_seconds(
                            pending, processed, judged, cycle_latency_ms, self.listener, concurrency
                        ),
                    )
                )

        cursor_target = last_success if failed else str(latest)
        if cursor_target != cursor:
            self.listener_svc.advance_judge_cursor(self.listener, item_id=cursor_target)
        return JudgeResult(judged=judged, hits=hits)

    def _retry_stuck_pending(self) -> None:
        """Re-fire instant delivery for any hit Event left undelivered by a
        previous failed cycle (instant listeners don't run the digest sweep)."""
        for stuck in self.event_svc.list_pending_for_listener(kind=EventKind.HIT, listener_id=str(self.listener.id)):
            try:
                obs = hydrate_event(stuck)
                self.delivery_svc.deliver_instant(stuck, obs, self.listener, self.config)
            except UnhydrateableObservation as exc:
                # Permanent: the connector was renamed/removed; this stuck
                # Event can never hydrate. Skip and continue; operator
                # can clean up the orphaned row.
                logger.warning(
                    "stuck-pending un-hydrateable listener=%s event=%s: %s",
                    self.listener.id,
                    stuck.id,
                    exc,
                )
            except _RECOVERABLE_ERRORS as exc:
                logger.warning(
                    "stuck-pending retry failed listener=%s event=%s err=%s: %s",
                    self.listener.id,
                    stuck.id,
                    type(exc).__name__,
                    exc,
                )

    def _persist_and_deliver(
        self,
        item: FeedItem,
        obs: Observation,
        result: JudgmentResult,
    ) -> "_ItemOutcome":
        """Apply a JudgmentResult: persist a hit Event if the score
        clears the threshold, and (for instant-mode listeners) fire
        delivery synchronously. Returns the data `run()` needs to
        update running cycle stats and emit progress.

        Split out from the engine call so the batch path can submit
        N judges in parallel and then handle persist + delivery in
        submission order (Django ORM stays sync, the LLM fan-out
        lives behind `engine.judge_batch`).

        The `hit` field means "a new Event landed," not just "the
        engine scored above threshold." When the unique constraint
        refuses a dedup re-emit the line shouldn't claim HIT, otherwise
        the cycle's hits counter, the HIT markers, and the new Event
        rows would disagree."""
        is_hit = result.score >= self.config.hit_threshold

        new_event_persisted = False
        if is_hit:
            event = self.event_svc.persist(item, self.listener, kind=EventKind.HIT, score=result.score)
            if event is not None:
                new_event_persisted = True
                if self.is_instant and self.config.notifiers:
                    self.delivery_svc.deliver_instant(event, obs, self.listener, self.config)

        return _ItemOutcome(obs=obs, score=result.score, hit=new_event_persisted, latency_ms=result.latency_ms)


def judge_listener(
    listener: Listener,
    *,
    on_progress: JudgeProgressCallback | None = None,
) -> JudgeResult | None:
    """Locked entry point for a single Listener's judgment cycle.

    Acquires `poll_lock(listener.id)`; returns None if another process holds
    it (caller records a skip), else the `JudgeResult`. Tests/debug paths
    that want to bypass the lock call `JudgeListenerOperation(...).run()`.
    """
    with poll_lock(str(listener.id)) as acquired:
        if not acquired:
            return None
        return JudgeListenerOperation(listener, on_progress=on_progress).run()
