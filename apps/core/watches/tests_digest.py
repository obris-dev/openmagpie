from datetime import timedelta
from unittest import mock

import ulid
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from pydantic import ValidationError

from feeds.models import FeedItem
from openmagpie_schema.watch import build_watch_action_input
from openmagpie_schema.watch_actions import LogConfig
from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionRunState
from watches.actions.log import LogAction
from watches.actions.protocol import ActionResult
from watches.models import WatchAction, WatchActionDigestWindow, WatchActionRun
from watches.operations.digest_flush import WatchDigestFlushOperation
from watches.operations.drain import WatchDrainOperation
from watches.policy import PolicyError
from watches.services import WatchActionRunService, WatchDigestWindowService, WatchService


class DigestDeliveryTests(TestCase):
    """A digest delivery batches a fixed window: the drain advances into
    the window (runs excluded from per-item draining), the flush emits one
    batch when the window is due and closes it."""

    _DIGEST_CFG = {"delivery": "digest", "digest_interval_seconds": 3600}

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)
        self.run_svc = WatchActionRunService(account_id=self.account_id)

    def _drain(self, now) -> None:
        for run in WatchActionRunService.Global.claim_due(now=now):
            WatchDrainOperation(run, now=now).run()

    def _count(self, action_id: str, state: str) -> int:
        return WatchActionRun.objects.filter(action_id=action_id, state=state).count()

    def _flush_due(self, now) -> None:
        for w in WatchDigestWindowService.Global.iter_due(now=now):
            WatchDigestFlushOperation(w, now=now).run()

    def _digest_window_with_items(self, n: int, *, now):
        """Create a watch (instant log -> digest log), enqueue n items, drain
        the instant action so each advances into the digest window. Returns
        (watch, digest_action, window) with n pending digest runs."""
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[
                build_watch_action_input(kind="log", config={"prefix": "[f]"}),
                build_watch_action_input(
                    kind="log", config={"prefix": "[d]", "delivery": "digest", "digest_interval_seconds": 3600}
                ),
            ],
        )
        a0, a1 = WatchAction.objects.filter(path_id=watch.initial_path_id).order_by("rank")
        feed_id = ulid.ulid()
        for i in range(n):
            fi = FeedItem.objects.create(
                account_id=self.account_id,
                feed_id=feed_id,
                source_kind="x",
                external_id=f"e{i}",
                source_label="x",
                occurred_at=now,
                data={"title": f"t{i}"},
            )
            self.run_svc.enqueue(
                watch_id=str(watch.id), action_id=str(a0.id), feed_item_id=str(fi.id), scheduled_at=now
            )
        self._drain(now)
        window = WatchActionDigestWindow.objects.get(account_id=self.account_id, action_id=str(a1.id))
        return watch, a1, window

    def test_advance_into_window_then_flush(self) -> None:
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(3, now=now)
        self.assertEqual(self._count(str(a1.id), "pending"), 3)
        self.assertIsNotNone(window.close_at)

        # The per-item drain must SKIP the digest runs (they're the flush's).
        self._drain(now)
        self.assertEqual(self._count(str(a1.id), "pending"), 3)

        # Not due yet (window closes in the future) -> no flush.
        for w in WatchDigestWindowService.Global.iter_due(now=now):
            WatchDigestFlushOperation(w, now=now).run()
        self.assertEqual(self._count(str(a1.id), "pending"), 3)

        # Due -> one batch, all succeeded, window closed.
        later = window.close_at + timedelta(seconds=1)
        for w in WatchDigestWindowService.Global.iter_due(now=later):
            WatchDigestFlushOperation(w, now=later).run()
        self.assertEqual(self._count(str(a1.id), "succeeded"), 3)
        window.refresh_from_db()
        self.assertIsNone(window.close_at)

    def test_persistent_failure_exhausts_and_closes_window(self) -> None:
        # A down destination must not spiral: each failed flush burns one
        # attempt ; after WATCH_RUN_MAX_ATTEMPTS the batch goes terminal FAILED
        # and the window closes, instead of re-emitting a growing batch forever.
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(2, now=now)
        max_attempts = settings.WATCH_RUN_MAX_ATTEMPTS
        later = window.close_at + timedelta(seconds=1)

        with mock.patch.object(LogAction, "run", side_effect=RuntimeError("destination down")):
            for _ in range(max_attempts):
                # Window stays due (open + past close_at) until it goes terminal.
                self.assertEqual(self._count(str(a1.id), "pending"), 2)
                due = list(WatchDigestWindowService.Global.iter_due(now=later))
                self.assertEqual(len(due), 1)
                for w in due:
                    WatchDigestFlushOperation(w, now=later).run()

        # Exhausted: every run terminal FAILED, none left pending, window closed.
        self.assertEqual(self._count(str(a1.id), "failed"), 2)
        self.assertEqual(self._count(str(a1.id), "pending"), 0)
        failed = WatchActionRun.objects.filter(action_id=str(a1.id), state="failed")
        self.assertTrue(all(r.attempts == max_attempts for r in failed))
        window.refresh_from_db()
        self.assertIsNone(window.close_at)
        # And it's no longer due (won't be re-flushed).
        self.assertEqual(list(WatchDigestWindowService.Global.iter_due(now=later)), [])

    def test_delete_watch_removes_digest_windows(self) -> None:
        # Digest windows are keyed on action_id, so they don't fall out of the
        # watch/path deletes ; WatchService.delete must clear them explicitly.
        now = timezone.now()
        watch, a1, _ = self._digest_window_with_items(1, now=now)
        self.assertTrue(WatchActionDigestWindow.objects.filter(action_id=str(a1.id)).exists())
        self.wsvc.delete(watch)
        self.assertFalse(WatchActionDigestWindow.objects.filter(action_id=str(a1.id)).exists())

    def test_remove_digest_action_clears_its_window(self) -> None:
        # Removing the digest action itself (allowed: the filter stays head)
        # must drop its window row, mirroring delete/edit-away cleanup.
        now = timezone.now()
        _, a1, _ = self._digest_window_with_items(1, now=now)
        a1_id = str(a1.id)  # capture before remove() nulls a1.id in memory
        self.assertTrue(WatchActionDigestWindow.objects.filter(action_id=a1_id).exists())
        self.wsvc.action_svc.remove(a1)
        self.assertFalse(WatchActionDigestWindow.objects.filter(action_id=a1_id).exists())
        self.assertFalse(WatchAction.objects.filter(id=a1_id).exists())

    def test_digest_to_instant_does_not_strand_runs(self) -> None:
        # Switching an action digest->instant must drop its window row ; else
        # claim_due (which excludes a run while its action has a window) would
        # strand the now-instant runs PENDING forever.
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(2, now=now)
        self.wsvc.action_svc.set_config(a1, spec=build_watch_action_input(kind="log", config={"prefix": "[d]"}))
        self.assertFalse(WatchActionDigestWindow.objects.filter(action_id=str(a1.id)).exists())
        # The previously-excluded runs now drain normally once due.
        later = window.close_at + timedelta(seconds=1)
        self._drain(later)
        self.assertEqual(self._count(str(a1.id), "succeeded"), 2)

    @override_settings(DIGEST_MAX_BATCH_ITEMS=2)
    def test_oversized_window_drains_fully_in_one_flush(self) -> None:
        # A window larger than the cap emits cap-sized slices (bounds peak
        # memory / payload) but loops them WITHIN a single flush, so the window
        # ends empty and closed — it does not wait for N cron cycles.
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(5, now=now)
        self.assertEqual(self._count(str(a1.id), "pending"), 5)
        later = window.close_at + timedelta(seconds=1)

        # A window spanning >1 slice logs N/total progress so a long drain
        # doesn't look stuck (5 items at cap=2 -> slices 2,2,1).
        with self.assertLogs("watches", level="INFO") as logs:
            self._flush_due(later)
        progress = [m for m in logs.output if "drain progress" in m]
        self.assertTrue(any("2/5" in m for m in progress), progress)
        self.assertTrue(any("4/5" in m for m in progress), progress)
        self.assertTrue(any("drain complete: 5 items" in m for m in logs.output), logs.output)

        self.assertEqual(self._count(str(a1.id), "succeeded"), 5)
        self.assertEqual(self._count(str(a1.id), "pending"), 0)
        window.refresh_from_db()
        self.assertIsNone(window.close_at)  # fully drained -> closed

    @override_settings(DIGEST_MAX_BATCH_ITEMS=1)
    def test_failing_run_does_not_block_fresh_run(self) -> None:
        # A run that already failed a transient flush (attempts>0) must not
        # head-of-line block a fresh (attempts=0) run: the gather is least-tried
        # -first, so the fresh run delivers this pass while the poison sinks.
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(2, now=now)
        # Pick either run as the already-retried "poison" — which feed item a
        # digest run maps to isn't deterministic (the instant-drain ties on
        # scheduled_at) and doesn't matter here. Mark it attempts=1 (< cap) and
        # key the failing mock on THAT run's item, so what's under test is the
        # gather's least-tried-first ordering, not the setup.
        poison, fresh = WatchActionRun.objects.filter(action_id=str(a1.id)).order_by("id")
        WatchActionRun.objects.filter(id=poison.id).update(attempts=1)
        poison_title = FeedItem.objects.get(id=poison.feed_item_id).data["title"]
        later = window.close_at + timedelta(seconds=1)

        def fail_poison(action, *, items, context):
            if any(it.data.get("title") == poison_title for it in items):
                raise RuntimeError("poison item")
            return ActionResult(state=WatchActionRunState.SUCCEEDED)

        with mock.patch.object(LogAction, "run", side_effect=fail_poison):
            self._flush_due(later)

        # The fresh (never-tried) run delivered this pass; the poison sank
        # behind it, burning one attempt, still pending for a later flush.
        fresh.refresh_from_db()
        poison.refresh_from_db()
        self.assertEqual(fresh.state, "succeeded")
        self.assertEqual(poison.state, "pending")
        self.assertEqual(poison.attempts, 2)

    def test_window_deleted_mid_emit_still_commits_batch(self) -> None:
        # Race: an operator mutates the action away from digest (removal /
        # digest->instant) and _clear_digest_windows DELETEs the window row
        # while the flush's emit POST is in flight. close_if_drained must not
        # raise DoesNotExist out of the terminal txn (which would roll back the
        # already-emitted batch and re-deliver via the instant path).
        now = timezone.now()
        _, a1, window = self._digest_window_with_items(2, now=now)
        later = window.close_at + timedelta(seconds=1)

        def emit_then_delete(action, *, items, context):
            # Simulate the concurrent mutate-away committing during the POST.
            WatchActionDigestWindow.objects.filter(action_id=str(a1.id)).delete()
            return ActionResult(state=WatchActionRunState.SUCCEEDED)

        with mock.patch.object(LogAction, "run", side_effect=emit_then_delete):
            self._flush_due(later)

        # Batch committed SUCCEEDED (not rolled back to PENDING) and no crash.
        self.assertEqual(self._count(str(a1.id), "succeeded"), 2)
        self.assertEqual(self._count(str(a1.id), "pending"), 0)
        self.assertFalse(WatchActionDigestWindow.objects.filter(action_id=str(a1.id)).exists())

    def test_digest_zero_interval_rejected_by_schema(self) -> None:
        # The presence rule (DIGEST requires a positive interval) is a pure
        # invariant enforced in the shared schema, so the CLI catches it
        # locally without a server round-trip. (The magnitude bound lives in
        # server policy ; see test_digest_interval_must_be_in_bounds.)
        with self.assertRaises(ValidationError):
            LogConfig(delivery=DeliveryCadence.DIGEST, digest_interval_seconds=0)
        # Instant ignores the interval, so 0 is fine there.
        self.assertFalse(LogConfig(delivery=DeliveryCadence.INSTANT, digest_interval_seconds=0).is_digest())

    def test_digest_interval_must_be_in_bounds(self) -> None:
        with self.assertRaises(PolicyError):
            self.wsvc.create(
                user_id=ulid.ulid(),
                name="t",
                feed_ids=[],
                actions=[
                    build_watch_action_input(kind="log", config={"prefix": "[f]"}),
                    build_watch_action_input(kind="log", config={"delivery": "digest", "digest_interval_seconds": 1}),
                ],
            )

    def test_digest_allowed_after_head(self) -> None:
        # The rule is positional, not categorical: a digest is fine once a
        # preceding action exists (add appends past rank 0, set_config edits
        # a non-head row).
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config={"prefix": "[f]"})],
        )
        asvc = self.wsvc.action_svc
        added = asvc.add(
            path_id=watch.initial_path_id, action=build_watch_action_input(kind="log", config=self._DIGEST_CFG)
        )
        self.assertEqual(added.rank, 1)
        # And editing that non-head row's config stays allowed.
        asvc.set_config(added, spec=build_watch_action_input(kind="log", config={**self._DIGEST_CFG, "prefix": "[d]"}))
