"""WatchBackfillOperation (the processor): source resolution, additive vs
replace (whole-chain), delete-once, pruned skip, chain-head source, permanent
failure. Split from tests_backfill.py (endpoint + Global) for the 350-line cap."""

from datetime import timedelta

import ulid
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, FeedItem
from watches.models import WatchAction, WatchActionBackfill, WatchActionRun
from watches.operations.backfill import WatchBackfillOperation
from watches.tests_backfill import _two_log_chain


class BackfillOperationTests(TestCase):
    """The processor: source resolution, additive vs replace (whole-chain),
    delete-once, pruned skip, chain-head source, permanent failure."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="op@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        body = _two_log_chain(self.client)
        self.watch_id = body["id"]
        self.source_id = body["actions"][0]["id"]
        self.target_id = body["actions"][1]["id"]
        self.account_id = str(WatchAction.objects.get(id=self.target_id).account_id)
        self.feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="F")

    def _item(self, *, occurred_at=None) -> str:
        item = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=self.feed.id,
            source_kind="rss",
            external_id=ulid.ulid(),
            source_label="X",
            data={"title": "t", "url": "https://x.test/a", "kind": "rss"},
            occurred_at=occurred_at,
        )
        return str(item.id)

    def _run(self, action_id: str, feed_item_id: str, *, state: str, completed_at=None) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=self.watch_id,
            action_id=action_id,
            kind="log",
            feed_item_id=feed_item_id,
            state=state,
            scheduled_at=timezone.now(),
            completed_at=completed_at,
        )

    def _job(self, **overrides) -> WatchActionBackfill:
        now = timezone.now()
        fields = {
            "account_id": self.account_id,
            "watch_id": self.watch_id,
            "target_action_id": self.target_id,
            "source_action_id": self.source_id,
            "source_is_head": False,
            "kind": "log",
            "replace": False,
            "completed_since": now - timedelta(days=30),
            "state": "running",
            "scheduled_at": now,
            "started_at": now,
            "attempts": 1,
        }
        fields.update(overrides)
        return WatchActionBackfill.objects.create(**fields)

    def _target_runs(self):
        return WatchActionRun.objects.filter(account_id=self.account_id, action_id=self.target_id)

    def test_additive_enqueues_only_missing(self) -> None:
        done, missing = self._item(), self._item()
        self._run(self.source_id, done, state="succeeded", completed_at=timezone.now())
        self._run(self.source_id, missing, state="succeeded", completed_at=timezone.now())
        self._run(self.target_id, done, state="succeeded", completed_at=timezone.now())  # already processed

        counts = WatchBackfillOperation(self._job(replace=False)).run()
        assert counts is not None
        self.assertEqual(counts.matched, 2)
        self.assertEqual(counts.enqueued, 1)  # only `missing`
        self.assertEqual(counts.deleted, 0)
        self.assertTrue(self._target_runs().filter(feed_item_id=missing, state="pending").exists())
        self.assertTrue(self._target_runs().filter(feed_item_id=done, state="succeeded").exists())

    def test_replace_deletes_terminal_then_reenqueues(self) -> None:
        item = self._item()
        self._run(self.source_id, item, state="succeeded", completed_at=timezone.now())
        old = self._run(self.target_id, item, state="succeeded", completed_at=timezone.now())  # old-format output

        counts = WatchBackfillOperation(self._job(replace=True)).run()
        assert counts is not None
        self.assertEqual(counts.deleted, 1)
        self.assertEqual(counts.enqueued, 1)
        self.assertFalse(WatchActionRun.objects.filter(id=old.id).exists())  # old deleted
        self.assertTrue(self._target_runs().filter(feed_item_id=item, state="pending").exists())

    def test_replace_regenerates_the_whole_chain(self) -> None:
        # Add a third action so the target (rank 1) has a downstream (rank 2); replace
        # must clear downstream too (its output derived from the target's old result).
        add = self.client.post(
            f"/v1/watches/{self.watch_id}/actions", {"kind": "log", "config": {"prefix": "[C]"}}, format="json"
        )
        self.assertEqual(add.status_code, 201, add.content)
        downstream_id = add.json()["action"]["id"]
        item = self._item()
        self._run(self.source_id, item, state="succeeded", completed_at=timezone.now())
        self._run(self.target_id, item, state="succeeded", completed_at=timezone.now())
        stale = self._run(downstream_id, item, state="succeeded", completed_at=timezone.now())

        counts = WatchBackfillOperation(self._job(replace=True)).run()
        assert counts is not None
        self.assertEqual(counts.deleted, 2)  # target + downstream, from --replace alone
        self.assertFalse(WatchActionRun.objects.filter(id=stale.id).exists())

    def test_preview_would_delete_counts_downstream(self) -> None:
        # The dry-run preview's would_delete must match the eventual `deleted`:
        # target + downstream terminal runs (should-fix -- was target-only).
        add = self.client.post(
            f"/v1/watches/{self.watch_id}/actions", {"kind": "log", "config": {"prefix": "[C]"}}, format="json"
        )
        downstream_id = add.json()["action"]["id"]
        item = self._item()
        self._run(self.source_id, item, state="succeeded", completed_at=timezone.now())
        self._run(self.target_id, item, state="succeeded", completed_at=timezone.now())
        self._run(downstream_id, item, state="succeeded", completed_at=timezone.now())
        resp = self.client.post(
            f"/v1/actions/{self.target_id}/backfill?dry_run=true",
            {"replace": True, "completed_since": "30d"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertTrue(body["dry_run"])  # server-honored marker
        self.assertEqual(body["would_delete"], 2)  # target + downstream

    def test_delete_once_skips_delete_on_retry(self) -> None:
        item = self._item()
        self._run(self.source_id, item, state="succeeded", completed_at=timezone.now())
        self._run(self.target_id, item, state="succeeded", completed_at=timezone.now())  # old output to delete
        job = self._job(replace=True)
        WatchBackfillOperation(job).run()  # first pass: deletes the old run + enqueues, stamps marker
        job.refresh_from_db()
        self.assertIsNotNone(job.replace_deleted_at)
        self.assertEqual(job.deleted, 1)
        # Simulate a drain regenerating the target run to SUCCEEDED, then a retry.
        self._target_runs().filter(feed_item_id=item).update(state="succeeded", completed_at=timezone.now())
        job.state = "running"
        job.save(update_fields=["state"])
        counts = WatchBackfillOperation(job).run()
        assert counts is not None
        self.assertEqual(counts.deleted, 1)  # reports the stored count, not a re-delete
        self.assertTrue(self._target_runs().filter(feed_item_id=item, state="succeeded").exists())  # NOT wiped

    def test_pruned_items_are_skipped(self) -> None:
        present = self._item()
        pruned = ulid.ulid()  # no FeedItem row
        self._run(self.source_id, present, state="succeeded", completed_at=timezone.now())
        self._run(self.source_id, pruned, state="succeeded", completed_at=timezone.now())

        counts = WatchBackfillOperation(self._job(replace=False)).run()
        assert counts is not None
        self.assertEqual(counts.matched, 2)
        self.assertEqual(counts.present, 1)
        self.assertEqual(counts.pruned, 1)
        self.assertEqual(counts.enqueued, 1)
        self.assertFalse(self._target_runs().filter(feed_item_id=pruned).exists())

    def test_chain_head_source_uses_feed_items(self) -> None:
        # A watch subscribed to a feed, single log action (rank 0 = head).
        head_feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="H")
        resp = self.client.post(
            "/v1/watches",
            {"name": "h", "feed_ids": [str(head_feed.id)], "actions": [{"kind": "log", "config": {"prefix": "[H]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        head_watch = resp.json()["id"]
        head_action = resp.json()["actions"][0]["id"]
        now = timezone.now()
        in_window = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=head_feed.id,
            source_kind="rss",
            external_id=ulid.ulid(),
            source_label="X",
            data={"title": "t", "kind": "rss"},
            occurred_at=now - timedelta(days=1),
        )
        FeedItem.objects.create(  # outside the window -> excluded
            account_id=self.account_id,
            feed_id=head_feed.id,
            source_kind="rss",
            external_id=ulid.ulid(),
            source_label="X",
            data={"title": "t", "kind": "rss"},
            occurred_at=now - timedelta(days=90),
        )
        job = WatchActionBackfill.objects.create(
            account_id=self.account_id,
            watch_id=head_watch,
            target_action_id=head_action,
            source_action_id="",
            source_is_head=True,
            kind="log",
            replace=False,
            occurred_since=now - timedelta(days=7),
            state="running",
            scheduled_at=now,
            started_at=now,
            attempts=1,
        )
        counts = WatchBackfillOperation(job).run()
        assert counts is not None
        self.assertEqual(counts.matched, 1)  # only the in-window item
        self.assertTrue(
            WatchActionRun.objects.filter(
                action_id=head_action, feed_item_id=str(in_window.id), state="pending"
            ).exists()
        )

    def test_target_gone_fails_permanently(self) -> None:
        job = self._job(target_action_id=ulid.ulid())  # no such action
        counts = WatchBackfillOperation(job).run()
        self.assertIsNone(counts)
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertTrue(job.error)


class BackfillLargeEnqueueTests(TransactionTestCase):
    """A >chunk-size backfill in AUTOCOMMIT (TransactionTestCase, like the real cron;
    a plain TestCase wraps each test in a transaction, which would mask a dangling
    server-side cursor). Guards the enqueue against streaming the source across the
    per-chunk bulk_create commits."""

    def setUp(self) -> None:
        user = SignupOperation(email="big@example.com", password="Str0ng-Passw0rd!").run()
        client = APIClient()
        client.force_authenticate(user=user)
        body = _two_log_chain(client)
        self.watch_id = body["id"]
        self.source_id = body["actions"][0]["id"]
        self.target_id = body["actions"][1]["id"]
        self.account_id = str(WatchAction.objects.get(id=self.target_id).account_id)
        self.feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="F")

    def test_enqueues_more_than_one_chunk(self) -> None:
        n = 550  # > the 500 enqueue page, so the source streams across >1 page + commit
        items = FeedItem.objects.bulk_create(
            [
                FeedItem(
                    account_id=self.account_id,
                    feed_id=self.feed.id,
                    source_kind="rss",
                    external_id=ulid.ulid(),
                    source_label="X",
                    data={"title": "t", "kind": "rss"},
                )
                for _ in range(n)
            ]
        )
        now = timezone.now()
        WatchActionRun.objects.bulk_create(
            [
                WatchActionRun(
                    account_id=self.account_id,
                    watch_id=self.watch_id,
                    action_id=self.source_id,
                    kind="log",
                    feed_item_id=str(it.id),
                    state="succeeded",
                    scheduled_at=now,
                    completed_at=now,
                )
                for it in items
            ]
        )
        counts = WatchBackfillOperation(self._job_now()).run()
        assert counts is not None
        self.assertEqual(counts.matched, n)
        self.assertEqual(counts.enqueued, n)  # every chunk enqueued; no cursor dangle

    def _job_now(self) -> WatchActionBackfill:
        now = timezone.now()
        return WatchActionBackfill.objects.create(
            account_id=self.account_id,
            watch_id=self.watch_id,
            target_action_id=self.target_id,
            source_action_id=self.source_id,
            kind="log",
            replace=False,
            completed_since=now - timedelta(days=30),
            state="running",
            scheduled_at=now,
            started_at=now,
            attempts=1,
        )
