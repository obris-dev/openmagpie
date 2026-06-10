"""HTTP-route tests for the delivery audit: the list nested under the action
(`/v1/actions/<id>/deliveries`) and the by-own-id detail
(`/v1/action-deliveries/<id>`). The recording / service logic is in
`tests_delivery.py`."""

import ulid
from django.test import TestCase
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from watches.models import WatchAction, WatchActionDelivery


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
        resp = self.client.get(f"/v1/action-deliveries/{delivery_id}")
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
        self.assertEqual(self.client.get(f"/v1/action-deliveries/{ulid.ulid()}").status_code, 404)
