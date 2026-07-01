from datetime import timedelta

import ulid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import Feed, FeedItem
from watches.models import WatchAction, WatchActionRun


class LeafActionRouteTests(TestCase):
    """Per-action endpoints addressed by the action's own id at
    `/v1/actions/<action_id>` (no watch id): set / remove / activity, plus
    account isolation."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="leaf@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _make_watch_with_action(self) -> tuple[str, str]:
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        return body["id"], body["actions"][0]["id"]

    def test_runs_set_remove_by_action_id_only(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()

        # runs: 200 with an empty log (no runs yet), no watch id in the path.
        runs = self.client.get(f"/v1/actions/{action_id}/activity")
        self.assertEqual(runs.status_code, 200, runs.content)
        self.assertEqual(runs.json()["items"], [])

        # set: replace the config in place.
        put = self.client.put(f"/v1/actions/{action_id}", {"kind": "log", "config": {"prefix": "[B]"}}, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(put.json()["action"]["id"], action_id)

        # remove: 204, and it's gone.
        self.assertEqual(self.client.delete(f"/v1/actions/{action_id}").status_code, 204)
        self.assertFalse(WatchAction.objects.filter(id=action_id).exists())

    def test_get_returns_action_definition(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()
        resp = self.client.get(f"/v1/actions/{action_id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["id"], action_id)
        self.assertEqual(body["kind"], "log")
        self.assertIn("config", body)
        self.assertIn("summary", body)

    def test_unknown_action_id_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{ulid.ulid()}/activity").status_code, 404)
        self.assertEqual(self.client.get(f"/v1/actions/{ulid.ulid()}").status_code, 404)

    def test_unrenderable_kind_action_get_is_404_not_500(self) -> None:
        # A persisted action whose stored kind is no longer known (a removed kind /
        # manual corruption) can't be rendered (no union member); the detail GET
        # 404s like the run-detail view rather than 500-ing on the None wire.
        _watch_id, action_id = self._make_watch_with_action()
        WatchAction.objects.filter(id=action_id).update(kind="removed_kind")
        self.assertEqual(self.client.get(f"/v1/actions/{action_id}").status_code, 404)

    def test_another_account_cannot_reach_the_action(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="other@example.com", password="Str0ng-Passw0rd!").run())
        # Same opaque 404 whether the action is absent or owned by someone else.
        self.assertEqual(other.get(f"/v1/actions/{action_id}/activity").status_code, 404)
        self.assertEqual(
            other.put(
                f"/v1/actions/{action_id}", {"kind": "log", "config": {"prefix": "x"}}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(other.delete(f"/v1/actions/{action_id}").status_code, 404)
        # ... and the owner's action is untouched.
        self.assertTrue(WatchAction.objects.filter(id=action_id).exists())


class ActionActivitySummaryTests(TestCase):
    """The activity summary windows the evaluated breakdown by COMPLETION
    (evaluation) time and reports the live pending/running backlog."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="summary@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id

    def _run(self, state: str, *, completed_at=None) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            kind="log",  # the run self-describes its kind so the wire can type its result
            feed_item_id=ulid.ulid(),
            state=state,
            scheduled_at=timezone.now(),
            completed_at=completed_at,
        )

    def test_evaluated_is_windowed_by_completion_backlog_is_live(self) -> None:
        now = timezone.now()
        self._run("succeeded", completed_at=now - timedelta(hours=1))  # in the 7d window
        self._run("gated", completed_at=now - timedelta(hours=2))  # in window
        self._run("succeeded", completed_at=now - timedelta(days=10))  # outside the window
        self._run("pending")  # backlog (no completion time)
        self._run("pending")
        self._run("running")
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"window": "7d"})
        self.assertEqual(resp.status_code, 200, resp.content)
        s = resp.json()["summary"]
        self.assertEqual(s["window"], "7d")  # echoes the requested preset
        # The 10-day-old succeeded run is excluded by the window.
        self.assertEqual(s["evaluated"], {"succeeded": 1, "gated": 1})
        self.assertEqual(s["pending"], 2)
        self.assertEqual(s["running"], 1)

    def test_retrying_backlog_is_failed_without_completion(self) -> None:
        # A transient FAILED still under the cap (no completed_at) is "retrying"
        # backlog — not "evaluated", not lost from the summary.
        now = timezone.now()
        self._run("failed", completed_at=None)  # retry-pending
        self._run("failed", completed_at=now)  # exhausted/terminal -> evaluated, not retrying
        self._run("pending")
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"window": "7d"})
        s = resp.json()["summary"]
        self.assertEqual(s["retrying"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["evaluated"], {"failed": 1})  # only the completed one

    def test_summary_omitted_while_paging(self) -> None:
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"after": ulid.ulid()})
        self.assertIsNone(resp.json()["summary"])

    def test_yesterday_is_a_closed_utc_day(self) -> None:
        # The only window with an `until` bound (a closed UTC calendar day),
        # so it exercises summary_for_action's completed_at__lt branch.
        now = timezone.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._run("succeeded", completed_at=midnight - timedelta(hours=12))  # yesterday midday -> in
        self._run("gated", completed_at=now)  # today -> excluded by the until bound
        self._run("failed", completed_at=midnight - timedelta(days=1, hours=1))  # before yesterday -> excluded
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity", {"window": "yesterday"})
        s = resp.json()["summary"]
        self.assertEqual(s["window"], "yesterday")
        self.assertEqual(s["evaluated"], {"succeeded": 1})

    def test_bad_window_is_400(self) -> None:
        self.assertEqual(
            self.client.get(f"/v1/actions/{self.action_id}/activity", {"window": "bogus"}).status_code, 400
        )


class ActionRunFeedItemTests(TestCase):
    """The runs response normalizes the judged items + their feeds into keyed
    side tables (`feed_items` by item id, `feeds` by feed id) instead of
    embedding them on each row. A run row is pure ids; a pruned item is simply
    absent from `feed_items` and the row still renders by `feed_item_id`."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="runitem@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id

    def _run(self, feed_item_id: str) -> None:
        WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            kind="log",  # the run self-describes its kind so the wire can type its result
            feed_item_id=feed_item_id,
            state="succeeded",
            scheduled_at=timezone.now(),
            completed_at=timezone.now(),
        )

    def test_items_and_feeds_maps_let_rows_key_in_and_tolerate_pruned(self) -> None:
        feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="Athletics")
        item = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=feed.id,
            source_kind="rss",
            external_id="ext-1",
            source_label="Example U",
            data={"title": "Coach hired", "url": "https://x.test/a", "kind": "rss"},
        )
        self._run(str(item.id))  # item present
        pruned_id = ulid.ulid()
        self._run(pruned_id)  # item absent (pruned)

        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()

        # Rows carry ids only (no embedded item); both ids are present as rows.
        row_item_ids = {r["feed_item_id"] for r in body["items"]}
        self.assertEqual(row_item_ids, {str(item.id), pruned_id})
        self.assertNotIn("feed_item", body["items"][0])

        # The present item is in `feed_items`, keyed by its id, and points at its feed.
        present = body["feed_items"][str(item.id)]
        self.assertEqual(present["title"], "Coach hired")
        self.assertEqual(present["url"], "https://x.test/a")
        self.assertEqual(present["source_label"], "Example U")
        self.assertEqual(present["feed_id"], str(feed.id))

        # The feed is normalized once into `feeds`, keyed by its id.
        self.assertEqual(body["feeds"][str(feed.id)]["name"], "Athletics")

        # The pruned item is absent from both maps; its row still renders by id.
        self.assertNotIn(pruned_id, body["feed_items"])
        self.assertNotIn("None", body["feeds"])


class ActionContextHeaderTests(TestCase):
    """The runs response carries the action being audited (kind + config), so a
    reader sees WHAT the runs were judged against (a semantic_filter's
    instructions + threshold) as a header, even with no runs yet."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="ctx@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {
                "name": "w",
                "feed_ids": [],
                "actions": [
                    {"kind": "semantic_filter", "config": {"instructions": "coach hires only", "threshold": 0.8}}
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.action_id = resp.json()["actions"][0]["id"]

    def test_response_carries_action_kind_and_config(self) -> None:
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        action = resp.json()["action"]
        self.assertEqual(action["id"], self.action_id)
        self.assertEqual(action["kind"], "semantic_filter")
        self.assertEqual(action["config"]["instructions"], "coach hires only")
        self.assertEqual(action["config"]["threshold"], 0.8)


class ActionActivityDetailTests(TestCase):
    """`GET /v1/action-activity/<id>`: one run in full, with the joined item /
    feed / action; a pruned item leaves those joins null; unknown id is 404."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="actdetail@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {
                "name": "w",
                "feed_ids": [],
                "actions": [{"kind": "semantic_filter", "config": {"instructions": "coach hires", "threshold": 0.8}}],
            },
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id

    def _run(self, feed_item_id: str) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            kind="semantic_filter",  # the run self-describes its kind so the wire types its result
            feed_item_id=feed_item_id,
            state="succeeded",
            scheduled_at=timezone.now(),
            completed_at=timezone.now(),
            result={"passed": True, "score": 0.9, "reason": "matched"},
        )

    def test_detail_joins_run_item_feed_action(self) -> None:
        feed = Feed.objects.create(account_id=self.account_id, user_id=ulid.ulid(), kind="rss", name="Athletics")
        item = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=feed.id,
            source_kind="rss",
            external_id="ext-1",
            source_label="Example U",
            data={"title": "Coach hired", "url": "https://x.test/a"},
        )
        run = self._run(str(item.id))
        resp = self.client.get(f"/v1/action-activity/{run.id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["run"]["id"], str(run.id))
        self.assertEqual(body["run"]["result"]["score"], 0.9)
        self.assertEqual(body["feed_item"]["title"], "Coach hired")
        self.assertEqual(body["feed_item"]["feed_id"], str(feed.id))
        self.assertEqual(body["feed"]["name"], "Athletics")
        self.assertEqual(body["action"]["kind"], "semantic_filter")

    def test_pruned_item_leaves_joins_null(self) -> None:
        pruned_id = ulid.ulid()
        run = self._run(pruned_id)  # no such item
        body = self.client.get(f"/v1/action-activity/{run.id}").json()
        self.assertIsNone(body["feed_item"])
        self.assertIsNone(body["feed"])
        self.assertEqual(body["run"]["feed_item_id"], pruned_id)

    def test_unknown_activity_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/action-activity/{ulid.ulid()}").status_code, 404)
