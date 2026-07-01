from datetime import timedelta

import ulid
from django.test import TestCase
from django.utils import timezone

from feeds.models import Feed, FeedItem
from openmagpie_schema.watch import build_watch_action_input
from openmagpie_schema.watch_enums import DeliveryCadence
from watches.models import WatchActionDigestWindow, WatchActionRun
from watches.operations.digest_flush import WatchDigestFlushOperation
from watches.operations.drain import WatchDrainOperation
from watches.operations.trigger import WatchTriggerOperation
from watches.services import WatchActionRunService, WatchDigestWindowService, WatchService


class DigestHeadTests(TestCase):
    """A digest delivery MAY be the chain's first action: the trigger opens
    its window (no preceding action to do it on advance), so the rank-0 runs
    batch instead of delivering instant. The old head-not-digest rule that
    forbade this is gone."""

    _DIGEST = {"delivery": "digest", "digest_interval_seconds": 3600}

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)

    def _count(self, action_id: str, state: str) -> int:
        return WatchActionRun.objects.filter(action_id=action_id, state=state).count()

    def _head_id(self, watch) -> str:
        return str(self.wsvc.action_svc.list_for_path(watch.initial_path_id)[0].id)

    def test_triggers_into_window_and_batches(self) -> None:
        # End-to-end: a digest HEAD has no preceding action to open its window,
        # so the trigger opens it. The enqueued rank-0 runs must batch (be
        # excluded from the per-item drain) and flush as one digest — NOT
        # deliver instant (the bug the old head-not-digest rule papered over).
        now = timezone.now()
        feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="x", name="f")
        for i in range(3):
            FeedItem.objects.create(
                account_id=self.account_id,
                feed_id=str(feed.id),
                source_kind="x",
                external_id=f"e{i}",
                source_label="x",
                occurred_at=now,
                data={"title": f"t{i}"},
            )
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[str(feed.id)],
            actions=[build_watch_action_input(kind="log", config={**self._DIGEST, "prefix": "[d]"})],
        )
        head_id = self._head_id(watch)

        WatchTriggerOperation(watch).run()

        # Trigger opened the window and enqueued the items as pending digest runs.
        window = WatchActionDigestWindow.objects.get(action_id=head_id)
        self.assertIsNotNone(window.close_at)
        self.assertEqual(self._count(head_id, "pending"), 3)
        later = window.close_at + timedelta(seconds=1)
        # The per-item drain must SKIP them (windowed action) -> not instant.
        for run in WatchActionRunService.Global.claim_due(now=later):
            WatchDrainOperation(run, now=later).run()
        self.assertEqual(self._count(head_id, "pending"), 3)
        # The flush emits them as one batch and closes the window.
        for w in WatchDigestWindowService.Global.iter_due(now=later):
            WatchDigestFlushOperation(w, now=later).run()
        self.assertEqual(self._count(head_id, "succeeded"), 3)
        window.refresh_from_db()
        self.assertIsNone(window.close_at)

    def test_can_be_created_first(self) -> None:
        # A digest as the sole/first action is allowed; create succeeds.
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config=self._DIGEST)],
        )
        head = self.wsvc.action_svc.list_for_path(watch.initial_path_id)[0]
        self.assertEqual(head.rank, 0)
        self.assertEqual(head.config.get("delivery"), DeliveryCadence.DIGEST.value)

    def test_can_be_added_as_head(self) -> None:
        # POST /actions (add) onto an empty chain clamps insert to rank 0 ; a
        # digest is allowed there now.
        watch = self.wsvc.create(user_id=ulid.ulid(), name="t", feed_ids=[], actions=[])
        added = self.wsvc.action_svc.add(
            path_id=watch.initial_path_id, action=build_watch_action_input(kind="log", config=self._DIGEST)
        )
        self.assertEqual(added.rank, 0)
        self.assertEqual(added.config.get("delivery"), DeliveryCadence.DIGEST.value)

    def test_can_be_set_on_head(self) -> None:
        # PUT /actions/<id> (set_config) editing the rank-0 row to a digest is
        # allowed; no chain lock needed (rank is irrelevant to the rule now).
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config={"prefix": "[f]"})],
        )
        head = self.wsvc.action_svc.list_for_path(watch.initial_path_id)[0]
        self.wsvc.action_svc.set_config(head, spec=build_watch_action_input(kind="log", config=self._DIGEST))
        head.refresh_from_db()
        self.assertEqual(head.config.get("delivery"), DeliveryCadence.DIGEST.value)

    def test_remove_promoting_digest_to_head_is_allowed(self) -> None:
        # Removing the filter from [filter, digest] leaves the digest as head
        # (rank 0) — allowed now; the trigger opens its window.
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[
                build_watch_action_input(kind="log", config={"prefix": "[f]"}),
                build_watch_action_input(kind="log", config={**self._DIGEST, "prefix": "[d]"}),
            ],
        )
        asvc = self.wsvc.action_svc
        a0, a1 = asvc.list_for_path(watch.initial_path_id)
        a1_id = str(a1.id)  # capture before remove()
        asvc.remove(a0)
        chain = asvc.list_for_path(watch.initial_path_id)
        self.assertEqual([str(a.id) for a in chain], [a1_id])
        self.assertEqual(chain[0].rank, 0)
