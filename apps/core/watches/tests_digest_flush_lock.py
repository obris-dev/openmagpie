"""`process_due_digests` under the per-window `digest_flush_lock`: a window
whose flush another pass holds is skipped (nothing emitted, runs still
PENDING) and picked up once the lock is free. The fence that keeps an
overlapping flush pass (a lapsed job lease) from emitting a batch twice."""

import io
from datetime import timedelta

import ulid
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from common.locks import digest_flush_lock
from feeds.models import FeedItem
from openmagpie_schema.watch import build_watch_action_input
from watches.models import WatchAction, WatchActionDigestWindow, WatchActionRun
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService, WatchService


class DigestFlushLockTests(TestCase):
    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)
        self.run_svc = WatchActionRunService(account_id=self.account_id)

    def _due_digest_action(self) -> str:
        """A watch (instant log -> digest log) with one run advanced into the
        digest window, and the window's close_at moved into the past so the
        command (which reads the real clock) sees it as due. Returns the digest
        action id."""
        now = timezone.now()
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
        fi = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=ulid.ulid(),
            source_kind="x",
            external_id="e0",
            source_label="x",
            occurred_at=now,
            data={"title": "t0"},
        )
        self.run_svc.enqueue(
            watch_id=str(watch.id), action_id=str(a0.id), kind=str(a0.kind), feed_item_id=str(fi.id), scheduled_at=now
        )
        for run in WatchActionRunService.Global.claim_due(now=now):
            WatchDrainOperation(run, now=now).run()  # advances the item into the digest window
        WatchActionDigestWindow.objects.filter(account_id=self.account_id, action_id=str(a1.id)).update(
            close_at=now - timedelta(seconds=1)
        )
        return str(a1.id)

    def _pending(self, action_id: str) -> int:
        return WatchActionRun.objects.filter(action_id=action_id, state="pending").count()

    def test_a_held_window_is_skipped_then_flushed_once_free(self) -> None:
        digest_id = self._due_digest_action()
        self.assertEqual(self._pending(digest_id), 1)

        out = io.StringIO()
        with digest_flush_lock(digest_id) as rival:  # another pass is mid-flush on this window
            self.assertTrue(rival)
            call_command("process_due_digests", stdout=out)
        self.assertIn("1 skipped", out.getvalue())
        self.assertEqual(self._pending(digest_id), 1)  # untouched: not emitted, not failed

        out = io.StringIO()
        call_command("process_due_digests", stdout=out)
        self.assertIn("0 skipped", out.getvalue())
        self.assertEqual(self._pending(digest_id), 0)  # flushed on the next pass
