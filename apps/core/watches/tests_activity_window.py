"""The run LIST's report-export filters: independent `[since, until)` windows on
the run's completion (`completed_*`) and the feed item's source time
(`occurred_*`), on `GET /v1/actions/<id>/activity`. Split from tests_activity.py
(file-length cap); the summary/list/detail behavior lives there."""

from datetime import timedelta

import ulid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, FeedItem
from watches.models import WatchAction, WatchActionRun


class ActionRunWindowFilterTests(TestCase):
    """The run LIST accepts independent `[since, until)` windows on the run's
    completion (`completed_*`) and the feed item's source time (`occurred_*`) -
    the report export's filters. Each value is a relative duration (`7d`) or an ISO
    datetime, resolved server-side; a bad value or an inverted window is a 400."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="window@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id
        self.feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="A")

    def _item(self, *, occurred_at) -> FeedItem:
        return FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=self.feed.id,
            source_kind="rss",
            external_id=ulid.ulid(),
            source_label="src",
            occurred_at=occurred_at,
            data={"title": "t", "url": "https://x.test/a", "external_url": "https://ext.test/a", "kind": "rss"},
        )

    def _run(self, *, feed_item_id, completed_at) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            kind="log",  # the run self-describes its kind so the wire can type its result
            feed_item_id=feed_item_id,
            state="succeeded",
            scheduled_at=timezone.now(),
            completed_at=completed_at,
        )

    def _item_ids(self, **params):
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", params)
        self.assertEqual(resp.status_code, 200, resp.content)
        return {r["feed_item_id"] for r in resp.json()["items"]}, resp

    def test_completed_window_bounds_rows(self) -> None:
        now = timezone.now()
        recent = self._run(feed_item_id=ulid.ulid(), completed_at=now - timedelta(hours=1))
        self._run(feed_item_id=ulid.ulid(), completed_at=now - timedelta(days=10))  # outside the window
        ids, _ = self._item_ids(completed_since=(now - timedelta(days=2)).isoformat())
        self.assertEqual(ids, {str(recent.feed_item_id)})

    def test_occurred_window_bounds_rows_and_excludes_null(self) -> None:
        now = timezone.now()
        in_item = self._item(occurred_at=now - timedelta(hours=1))
        out_item = self._item(occurred_at=now - timedelta(days=10))
        null_item = self._item(occurred_at=None)  # no source time -> excluded from an occurred window
        for it in (in_item, out_item, null_item):
            self._run(feed_item_id=str(it.id), completed_at=now)
        ids, _ = self._item_ids(occurred_since=(now - timedelta(days=2)).isoformat())
        self.assertEqual(ids, {str(in_item.id)})

    def test_completed_and_occurred_combine(self) -> None:
        now = timezone.now()
        recent_item = self._item(occurred_at=now - timedelta(hours=1))
        # Item occurred recently but the run completed long ago -> dropped by completed window.
        self._run(feed_item_id=str(recent_item.id), completed_at=now - timedelta(days=10))
        both = self._item(occurred_at=now - timedelta(hours=2))
        self._run(feed_item_id=str(both.id), completed_at=now - timedelta(hours=1))  # both recent -> kept
        since = (now - timedelta(days=2)).isoformat()
        ids, _ = self._item_ids(occurred_since=since, completed_since=since)
        self.assertEqual(ids, {str(both.id)})

    def test_external_url_surfaced_in_feed_items(self) -> None:
        now = timezone.now()
        it = self._item(occurred_at=now)
        self._run(feed_item_id=str(it.id), completed_at=now)
        _, resp = self._item_ids()
        self.assertEqual(resp.json()["feed_items"][str(it.id)]["external_url"], "https://ext.test/a")

    def test_bad_datetime_is_400(self) -> None:
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"completed_since": "not-a-date"})
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_out_of_range_datetime_is_400_not_500(self) -> None:
        # Well-formed shape but invalid value (Feb 30): fromisoformat RAISES, so it
        # must be caught into a 400, not escape as a 500.
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"completed_since": "2026-02-30T00:00:00Z"})
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_relative_duration_resolves_server_side(self) -> None:
        # A relative value (`7d`) is resolved HERE against the server clock, not
        # pre-resolved by the client; it bounds rows the same as an ISO datetime.
        now = timezone.now()
        recent = self._run(feed_item_id=ulid.ulid(), completed_at=now - timedelta(hours=1))
        self._run(feed_item_id=ulid.ulid(), completed_at=now - timedelta(days=10))  # outside the last 7d
        ids, _ = self._item_ids(completed_since="7d")
        self.assertEqual(ids, {recent.feed_item_id})

    def test_huge_duration_is_400_not_500(self) -> None:
        # A giant relative duration overflows timedelta; the resolver folds the
        # OverflowError into a 400 so crafted input never escapes as a 500.
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"completed_since": "999999999999w"})
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_row_window_skips_the_summary_rollup(self) -> None:
        # A row-windowed request (the export) discards the summary AND it'd be over a
        # different time basis (the `window` preset), so it's skipped -- no wasted
        # aggregation. (The window-less `activity list` still gets its summary.)
        self._run(feed_item_id=ulid.ulid(), completed_at=timezone.now() - timedelta(hours=1))
        windowed = self.client.get(f"/v1/actions/{self.action_id}/activity", {"completed_since": "7d"}).json()
        self.assertIsNone(windowed["summary"])
        plain = self.client.get(f"/v1/actions/{self.action_id}/activity").json()
        self.assertIsNotNone(plain["summary"])  # no row-window -> summary still computed

    def test_occurred_subquery_requires_a_bound(self) -> None:
        # The seam itself rejects both-None (which would match every account item),
        # so a future caller that forgets to pre-check can't silently scan all.
        from feeds.services import FeedItemService

        with self.assertRaises(ValueError):
            FeedItemService(account_id=self.account_id).occurred_window_id_subquery(since=None, until=None)

    def test_too_early_until_is_400_not_500(self) -> None:
        # A lone *_until so early that until - SPAN underflows datetime.min must be a
        # 400 (folded to ValueError), not an uncaught OverflowError -> 500.
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"occurred_until": "0001-01-04"})
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_inverted_window_is_400(self) -> None:
        now = timezone.now()
        resp = self.client.get(
            f"/v1/actions/{self.action_id}/activity",
            {"completed_since": now.isoformat(), "completed_until": (now - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(resp.status_code, 400, resp.content)
