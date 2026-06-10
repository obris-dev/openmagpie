from datetime import timedelta

import ulid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from feeds.models import FeedItem
from watches.models import WatchAction, WatchActionDelivery, WatchActionRun


class LeafActionRouteTests(TestCase):
    """Per-action endpoints addressed by the action's own id at
    `/v1/actions/<action_id>` (no watch id): set / remove / runs, plus
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
        runs = self.client.get(f"/v1/actions/{action_id}/runs")
        self.assertEqual(runs.status_code, 200, runs.content)
        self.assertEqual(runs.json()["items"], [])

        # set: replace the config in place.
        put = self.client.put(f"/v1/actions/{action_id}", {"kind": "log", "config": {"prefix": "[B]"}}, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(put.json()["id"], action_id)

        # remove: 204, and it's gone.
        self.assertEqual(self.client.delete(f"/v1/actions/{action_id}").status_code, 204)
        self.assertFalse(WatchAction.objects.filter(id=action_id).exists())

    def test_unknown_action_id_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{ulid.ulid()}/runs").status_code, 404)

    def test_another_account_cannot_reach_the_action(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="other@example.com", password="Str0ng-Passw0rd!").run())
        # Same opaque 404 whether the action is absent or owned by someone else.
        self.assertEqual(other.get(f"/v1/actions/{action_id}/runs").status_code, 404)
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
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "7d"})
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
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "7d"})
        s = resp.json()["summary"]
        self.assertEqual(s["retrying"], 1)
        self.assertEqual(s["pending"], 1)
        self.assertEqual(s["evaluated"], {"failed": 1})  # only the completed one

    def test_summary_omitted_while_paging(self) -> None:
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"after": ulid.ulid()})
        self.assertIsNone(resp.json()["summary"])

    def test_yesterday_is_a_closed_utc_day(self) -> None:
        # The only window with an `until` bound (a closed UTC calendar day),
        # so it exercises summary_for_action's completed_at__lt branch.
        now = timezone.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        self._run("succeeded", completed_at=midnight - timedelta(hours=12))  # yesterday midday -> in
        self._run("gated", completed_at=now)  # today -> excluded by the until bound
        self._run("failed", completed_at=midnight - timedelta(days=1, hours=1))  # before yesterday -> excluded
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "yesterday"})
        s = resp.json()["summary"]
        self.assertEqual(s["window"], "yesterday")
        self.assertEqual(s["evaluated"], {"succeeded": 1})

    def test_bad_window_is_400(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "bogus"}).status_code, 400)


class ActionDeliveriesRouteTests(TestCase):
    """`/v1/actions/<action_id>/deliveries`: the HTTP-call audit, addressed by
    the action's own id, account-scoped."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="deliv@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _webhook_action(self) -> tuple[str, str]:
        resp = self.client.post(
            "/v1/watches",
            {
                "name": "w",
                "feed_ids": [],
                "actions": [{"kind": "webhook", "config": {"url": "https://h.example.com/x"}}],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        return body["id"], body["actions"][0]["id"]

    def test_deliveries_lists_recorded_calls(self) -> None:
        watch_id, action_id = self._webhook_action()
        empty = self.client.get(f"/v1/actions/{action_id}/deliveries")
        self.assertEqual(empty.status_code, 200, empty.content)
        self.assertEqual(empty.json()["items"], [])

        action = WatchAction.objects.get(id=action_id)
        WatchActionDelivery.objects.create(
            account_id=action.account_id,
            watch_id=watch_id,
            action_id=action_id,
            delivery="instant",
            method="POST",
            target_host="h.example.com",
            state="succeeded",
            http_status=200,
            item_count=1,
            attempt=1,
            request_payload={"items": []},
        )
        resp = self.client.get(f"/v1/actions/{action_id}/deliveries")
        self.assertEqual(resp.status_code, 200, resp.content)
        (item,) = resp.json()["items"]
        self.assertEqual(item["state"], "succeeded")
        self.assertEqual(item["http_status"], 200)
        self.assertEqual(item["method"], "POST")
        self.assertEqual(item["item_count"], 1)
        self.assertNotIn("request_payload", item)  # lean list ; payload is on the detail only

    def _make_delivery(self, watch_id: str, action_id: str) -> str:
        action = WatchAction.objects.get(id=action_id)
        d = WatchActionDelivery.objects.create(
            account_id=action.account_id,
            watch_id=watch_id,
            action_id=action_id,
            delivery="instant",
            method="POST",
            target_host="h.example.com",
            state="succeeded",
            http_status=200,
            item_count=1,
            request_payload={"items": [{"key": "reddit:1"}]},
        )
        return str(d.id)

    def test_delivery_detail_includes_payload(self) -> None:
        watch_id, action_id = self._webhook_action()
        delivery_id = self._make_delivery(watch_id, action_id)
        resp = self.client.get(f"/v1/deliveries/{delivery_id}")
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body["id"], delivery_id)
        self.assertEqual(body["request_payload"], {"items": [{"key": "reddit:1"}]})

    def test_bad_state_is_400(self) -> None:
        _watch_id, action_id = self._webhook_action()
        bad = self.client.get(f"/v1/actions/{action_id}/deliveries", {"state": "bogus"})
        self.assertEqual(bad.status_code, 400, bad.content)

    def test_unknown_action_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{ulid.ulid()}/deliveries").status_code, 404)

    def test_unknown_delivery_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/deliveries/{ulid.ulid()}").status_code, 404)


class ActionRunFeedItemTests(TestCase):
    """The runs log joins the judged feed item (title / url / source_label),
    batched; a pruned item leaves `feed_item` null but keeps `feed_item_id` so
    the row still renders."""

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
            feed_item_id=feed_item_id,
            state="succeeded",
            scheduled_at=timezone.now(),
            completed_at=timezone.now(),
        )

    def test_run_carries_item_fields_and_tolerates_pruned(self) -> None:
        item = FeedItem.objects.create(
            account_id=self.account_id,
            feed_id=ulid.ulid(),
            source_kind="rss",
            external_id="ext-1",
            source_label="Example U",
            data={"title": "Coach hired", "url": "https://x.test/a", "kind": "rss"},
        )
        self._run(str(item.id))  # item present
        pruned_id = ulid.ulid()
        self._run(pruned_id)  # item absent (pruned)

        resp = self.client.get(f"/v1/actions/{self.action_id}/runs")
        self.assertEqual(resp.status_code, 200, resp.content)
        by_item = {r["feed_item_id"]: r for r in resp.json()["items"]}

        present = by_item[str(item.id)]["feed_item"]
        self.assertEqual(present["title"], "Coach hired")
        self.assertEqual(present["url"], "https://x.test/a")
        self.assertEqual(present["source_label"], "Example U")

        pruned = by_item[pruned_id]
        self.assertIsNone(pruned["feed_item"])
        self.assertEqual(pruned["feed_item_id"], pruned_id)


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
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs")
        self.assertEqual(resp.status_code, 200, resp.content)
        action = resp.json()["action"]
        self.assertEqual(action["id"], self.action_id)
        self.assertEqual(action["kind"], "semantic_filter")
        self.assertEqual(action["config"]["instructions"], "coach hires only")
        self.assertEqual(action["config"]["threshold"], 0.8)
