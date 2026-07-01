from datetime import timedelta
from unittest import mock

import httpx
import ulid
from django.test import TestCase
from django.utils import timezone

from feeds.models import FeedItem
from openmagpie_schema.watch import build_watch_action_input
from openmagpie_schema.watch_enums import WatchActionDeliveryState
from watches.models import WatchAction, WatchActionDelivery, WatchActionDigestWindow, WatchActionRun
from watches.operations.digest_flush import WatchDigestFlushOperation
from watches.operations.drain import WatchDrainOperation
from watches.services import WatchActionRunService, WatchDigestWindowService, WatchService

_URL = "https://h.example.com/hook"


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("POST", _URL))


def _http(status: int):
    """Patch the SSRF gate open + the transport to a fixed status."""
    return (
        mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
        mock.patch("watches.actions.webhook.httpx.request", return_value=_resp(status)),
    )


class WebhookDeliveryRecordTests(TestCase):
    """The drain/flush record one WatchActionDelivery per HTTP attempt, link
    the runs to it, and dedup a replayed digest batch. Mirrors tests_digest's
    setup; httpx is mocked so no network is touched."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)
        self.run_svc = WatchActionRunService(account_id=self.account_id)

    def _item(self, ext: str, now, **data) -> FeedItem:
        return FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=ulid.ulid(),
            source_kind="reddit_subreddit",
            external_id=ext,
            source_label="r/ClaudeAI",
            occurred_at=now,
            data={"source": "reddit", "external_id": ext, **data},
        )

    def _drain(self, now) -> None:
        for run in WatchActionRunService.Global.claim_due(now=now):
            WatchDrainOperation(run, now=now).run()

    def _flush_due(self, now) -> None:
        for w in WatchDigestWindowService.Global.iter_due(now=now):
            WatchDigestFlushOperation(w, now=now).run()

    def _digest_watch(self, n: int, *, now):
        """A log-head -> webhook-digest watch with n items advanced into the
        webhook's window. Returns (webhook_action, window)."""
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="ai-webhook",
            feed_ids=[],
            actions=[
                build_watch_action_input(kind="log", config={"prefix": "[f]"}),
                build_watch_action_input(
                    kind="webhook",
                    config={"url": _URL, "delivery": "digest", "digest_interval_seconds": 3600},
                ),
            ],
        )
        a0, a1 = WatchAction.objects.filter(path_id=watch.initial_path_id).order_by("rank")
        for i in range(n):
            fi = self._item(f"e{i}", now, title=f"t{i}")
            self.run_svc.enqueue(
                watch_id=str(watch.id),
                action_id=str(a0.id),
                kind=str(a0.kind),
                feed_item_id=str(fi.id),
                scheduled_at=now,
            )
        self._drain(now)  # log head succeeds -> each advances into the digest window
        window = WatchActionDigestWindow.objects.get(account_id=self.account_id, action_id=str(a1.id))
        return a1, window

    def test_instant_records_delivery_and_links_run(self) -> None:
        now = timezone.now()
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="ai-webhook",
            feed_ids=[],
            actions=[build_watch_action_input(kind="webhook", config={"url": _URL})],
        )
        action = WatchAction.objects.get(path_id=watch.initial_path_id)
        fi = self._item("e1", now, title="T")
        self.run_svc.enqueue(
            watch_id=str(watch.id),
            action_id=str(action.id),
            kind=str(action.kind),
            feed_item_id=str(fi.id),
            scheduled_at=now,
        )

        block, request = _http(200)
        with block, request:
            self._drain(now)

        delivery = WatchActionDelivery.objects.get(action_id=str(action.id))
        self.assertEqual(delivery.state, WatchActionDeliveryState.SUCCEEDED.value)
        self.assertEqual(delivery.http_status, 200)
        self.assertEqual(delivery.item_count, 1)
        self.assertEqual(delivery.method, "POST")
        run = WatchActionRun.objects.get(action_id=str(action.id))
        self.assertEqual(run.state, "succeeded")
        self.assertEqual(run.delivery_id, str(delivery.id))

    def test_digest_one_delivery_covers_whole_batch(self) -> None:
        now = timezone.now()
        a1, window = self._digest_watch(3, now=now)
        later = window.close_at + timedelta(seconds=1)

        block, request = _http(200)
        with block, request as req:
            self._flush_due(later)

        self.assertEqual(req.call_count, 1)  # ONE POST for the whole batch
        delivery = WatchActionDelivery.objects.get(action_id=str(a1.id))
        self.assertEqual(delivery.item_count, 3)
        self.assertEqual(delivery.state, WatchActionDeliveryState.SUCCEEDED.value)
        runs = WatchActionRun.objects.filter(action_id=str(a1.id))
        self.assertEqual(runs.count(), 3)
        self.assertTrue(all(r.state == "succeeded" and r.delivery_id == str(delivery.id) for r in runs))

    def test_digest_replay_is_at_least_once(self) -> None:
        # No server-side dedup: a crash AFTER the POST but BEFORE the runs were
        # marked leaves them pending, so the next flush re-POSTs (at-least-once).
        # Receivers dedup per item on the in-body `key` ; the server just logs a
        # second attempt.
        now = timezone.now()
        a1, window = self._digest_watch(2, now=now)
        later = window.close_at + timedelta(seconds=1)

        block, request = _http(200)
        with block, request as req:
            self._flush_due(later)
        self.assertEqual(req.call_count, 1)

        # Simulate the crash: reset the runs to pending and reopen the window.
        WatchActionRun.objects.filter(action_id=str(a1.id)).update(state="pending", completed_at=None, delivery_id="")
        window.refresh_from_db()
        window.close_at = now
        window.save(update_fields=["close_at"])

        block2, request2 = _http(200)
        with block2, request2 as req2:
            WatchDigestFlushOperation(window, now=now + timedelta(seconds=2)).run()

        self.assertEqual(req2.call_count, 1)  # re-POST (at-least-once), no server dedup
        self.assertEqual(WatchActionDelivery.objects.filter(action_id=str(a1.id)).count(), 2)  # one row per attempt
        self.assertEqual(WatchActionRun.objects.filter(action_id=str(a1.id), state="succeeded").count(), 2)

    def test_digest_failed_then_succeeded_logs_each_attempt(self) -> None:
        now = timezone.now()
        a1, window = self._digest_watch(2, now=now)
        later = window.close_at + timedelta(seconds=1)

        block, request = _http(503)  # transient
        with block, request:
            self._flush_due(later)
        self.assertEqual(
            WatchActionDelivery.objects.filter(
                action_id=str(a1.id), state=WatchActionDeliveryState.FAILED.value
            ).count(),
            1,
        )
        self.assertEqual(WatchActionRun.objects.filter(action_id=str(a1.id), state="pending").count(), 2)

        block2, request2 = _http(200)
        with block2, request2:
            self._flush_due(later)
        self.assertEqual(WatchActionDelivery.objects.filter(action_id=str(a1.id)).count(), 2)  # one row per attempt
        self.assertEqual(WatchActionRun.objects.filter(action_id=str(a1.id), state="succeeded").count(), 2)

    def test_log_action_makes_no_delivery_row(self) -> None:
        now = timezone.now()
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="logger",
            feed_ids=[],
            actions=[build_watch_action_input(kind="log", config={"prefix": "[f]"})],
        )
        action = WatchAction.objects.get(path_id=watch.initial_path_id)
        fi = self._item("e1", now, title="T")
        self.run_svc.enqueue(
            watch_id=str(watch.id),
            action_id=str(action.id),
            kind=str(action.kind),
            feed_item_id=str(fi.id),
            scheduled_at=now,
        )
        self._drain(now)
        self.assertEqual(WatchActionRun.objects.get(action_id=str(action.id)).state, "succeeded")
        self.assertEqual(WatchActionDelivery.objects.count(), 0)  # no HTTP call -> no row
