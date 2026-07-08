"""Backfill: endpoint validation + the queued job, the processor
(`WatchBackfillOperation`) additive/replace(whole-chain)/delete-once/head-source/fail
paths, and the cron Global (claim/reap). Chains use `log` actions; the backfill
mechanics are kind-agnostic (select source runs -> delete/enqueue target runs)."""

from datetime import timedelta

import ulid
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from watches.models import WatchAction, WatchActionBackfill
from watches.services import WatchActionBackfillService


def _two_log_chain(client: APIClient, *, feed_ids=None) -> dict:
    resp = client.post(
        "/v1/watches",
        {
            "name": "w",
            "feed_ids": feed_ids or [],
            "actions": [{"kind": "log", "config": {"prefix": "[A]"}}, {"kind": "log", "config": {"prefix": "[B]"}}],
        },
        format="json",
    )
    assert resp.status_code == 201, resp.content
    return resp.json()


class BackfillEndpointTests(TestCase):
    """POST /v1/actions/<id>/backfill validation + queue + dry-run preview."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="bf@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        body = _two_log_chain(self.client)
        self.source_id = body["actions"][0]["id"]  # rank 0
        self.target_id = body["actions"][1]["id"]  # rank 1
        self.account_id = str(WatchAction.objects.get(id=self.target_id).account_id)

    def _post(self, action_id: str, payload: dict, *, dry_run: bool = False):
        url = f"/v1/actions/{action_id}/backfill" + ("?dry_run=true" if dry_run else "")
        return self.client.post(url, payload, format="json")

    def test_no_window_is_400(self) -> None:
        resp = self._post(self.target_id, {"replace": False})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("windows", resp.json())

    def test_chain_head_completed_window_is_400(self) -> None:
        # The rank-0 action has no upstream run -> a completion window is meaningless.
        resp = self._post(self.source_id, {"completed_since": "30d"})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("windows", resp.json())

    def test_submit_queues_a_pending_job(self) -> None:
        resp = self._post(self.target_id, {"replace": True, "completed_since": "30d"})
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.assertEqual(body["state"], "pending")
        self.assertEqual(body["target_action_id"], self.target_id)
        self.assertEqual(body["source_action_id"], self.source_id)
        self.assertFalse(body["source_is_head"])
        self.assertTrue(body["replace"])
        self.assertTrue(WatchActionBackfill.objects.filter(id=body["id"], state="pending").exists())

    def test_replace_string_false_is_not_destructive(self) -> None:
        # A body with the STRING "false" must not trigger replace (bool("false") is
        # truthy); the BooleanField coerces it to False, falling back to additive.
        resp = self._post(self.target_id, {"replace": "false", "completed_since": "30d"})
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertFalse(resp.json()["replace"])

    def test_replace_string_true_enables_replace(self) -> None:
        # And the STRING "true" (a form-encoded client, where every value is a string)
        # DOES enable replace: the BooleanField coerces the spelling, unlike `is True`.
        resp = self._post(self.target_id, {"replace": "true", "completed_since": "30d"})
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(resp.json()["replace"])

    def test_dry_run_returns_preview_without_a_job(self) -> None:
        before = WatchActionBackfill.objects.count()
        resp = self._post(self.target_id, {"replace": True, "completed_since": "30d"}, dry_run=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertIn("matched", body)
        self.assertIn("would_enqueue", body)
        self.assertEqual(WatchActionBackfill.objects.count(), before)  # no job written

    def test_status_readback_and_account_isolation(self) -> None:
        job_id = self._post(self.target_id, {"completed_since": "30d"}).json()["id"]
        self.assertEqual(self.client.get(f"/v1/action-backfills/{job_id}").status_code, 200)
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="o@example.com", password="Str0ng-Passw0rd!").run())
        self.assertEqual(other.get(f"/v1/action-backfills/{job_id}").status_code, 404)
        self.assertEqual(
            other.post(f"/v1/actions/{self.target_id}/backfill", {"completed_since": "30d"}, format="json").status_code,
            404,
        )

    def test_list_returns_this_accounts_jobs(self) -> None:
        job_id = self._post(self.target_id, {"completed_since": "30d"}).json()["id"]
        resp = self.client.get("/v1/action-backfills")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([j["id"] for j in resp.json()["items"]], [job_id])
        # Another account sees none of it.
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="o2@example.com", password="Str0ng-Passw0rd!").run())
        self.assertEqual(other.get("/v1/action-backfills").json()["items"], [])


class BackfillGlobalTests(TestCase):
    """The cron Global: claim by CAS, reap stale RUNNING."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="g@example.com", password="Str0ng-Passw0rd!").run()
        client = APIClient()
        client.force_authenticate(user=self.user)
        body = _two_log_chain(client)
        self.account_id = str(WatchAction.objects.get(id=body["actions"][0]["id"]).account_id)
        self.svc = WatchActionBackfillService(account_id=self.account_id)

    def _job(self, **overrides) -> WatchActionBackfill:
        now = timezone.now()
        fields = {
            "account_id": self.account_id,
            "watch_id": ulid.ulid(),
            "target_action_id": ulid.ulid(),
            "source_action_id": ulid.ulid(),
            "kind": "log",
            "state": "pending",
            "scheduled_at": now,
        }
        fields.update(overrides)
        return WatchActionBackfill.objects.create(**fields)

    def test_claim_due_flips_pending_to_running(self) -> None:
        job = self._job()
        claimed = list(WatchActionBackfillService.Global.claim_due())
        self.assertEqual([c.id for c in claimed], [job.id])
        self.assertEqual(claimed[0].state, "running")
        self.assertEqual(claimed[0].attempts, 1)
        # A second claim finds nothing (already RUNNING).
        self.assertEqual(list(WatchActionBackfillService.Global.claim_due()), [])

    def test_reap_stale_running_back_to_failed_retryable(self) -> None:
        old = timezone.now() - timedelta(hours=1)
        job = self._job(state="running", started_at=old, attempts=1)
        reaped = WatchActionBackfillService.Global.reap_stale()
        self.assertEqual(reaped, 1)
        job.refresh_from_db()
        self.assertEqual(job.state, "failed")
        self.assertIsNone(job.completed_at)  # retryable -> re-claimable
