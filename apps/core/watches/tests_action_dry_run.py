"""`?dry_run=true` on the watch + single-action endpoints.

Validate + preview the would-be action(s), persist nothing. The single-action id
reflects PERSISTENCE, not the flag - an add preview has no id yet, an edit
preview keeps the existing action's (unchanged) id. Validation runs identically
(a bad config still 400s without persisting), and secrets are redacted on the
preview exactly as on a real write.
"""

from django.test import TestCase
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from watches.models import Watch, WatchAction


class WatchActionDryRunTests(TestCase):
    def setUp(self) -> None:
        self.user = SignupOperation(email="actiondryrun@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        self.watch_id = body["id"]
        self.action_id = body["actions"][0]["id"]

    def test_add_dry_run_previews_without_persisting(self) -> None:
        before = WatchAction.objects.count()
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/actions?dry_run=true",
            {"kind": "log", "config": {"prefix": "[NEW]"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)  # 200, not 201: nothing created
        data = resp.json()
        self.assertTrue(data["dry_run"])
        # The response nests the action node under `action`; an add preview isn't
        # persisted, so its id is empty.
        self.assertEqual(data["action"]["id"], "")
        self.assertEqual(data["action"]["config"]["prefix"], "[NEW]")  # the validated would-be config
        self.assertEqual(WatchAction.objects.count(), before)  # nothing persisted

    def test_edit_dry_run_keeps_id_and_does_not_persist(self) -> None:
        resp = self.client.put(
            f"/v1/actions/{self.action_id}?dry_run=true",
            {"kind": "log", "config": {"prefix": "[CHANGED]"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["action"]["id"], self.action_id)  # edit preview keeps the existing id (unchanged)
        self.assertEqual(data["action"]["config"]["prefix"], "[CHANGED]")  # the would-be config
        self.assertEqual(WatchAction.objects.get(id=self.action_id).config["prefix"], "[A]")  # DB unchanged

    def test_dry_run_still_validates(self) -> None:
        before = WatchAction.objects.count()
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/actions?dry_run=true",
            {"kind": "webhook", "config": {"method": "POST"}},  # `url` is required -> invalid
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)  # same validation as a real add
        self.assertEqual(WatchAction.objects.count(), before)  # nothing persisted on the error path

    def test_extract_dry_run_previews_and_validates(self) -> None:
        # A valid extract action previews (200, nothing persisted)...
        before = WatchAction.objects.count()
        ok = self.client.post(
            f"/v1/watches/{self.watch_id}/actions?dry_run=true",
            {"kind": "extract", "config": {"fields": [{"name": "person", "description": "who"}]}},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertTrue(ok.json()["dry_run"])
        # ...and an empty field set fails validation the same as a real add.
        bad = self.client.post(
            f"/v1/watches/{self.watch_id}/actions?dry_run=true",
            {"kind": "extract", "config": {"fields": []}},
            format="json",
        )
        self.assertEqual(bad.status_code, 400, bad.content)
        self.assertEqual(WatchAction.objects.count(), before)  # neither persisted

    def test_dry_run_add_redacts_secrets(self) -> None:
        # A dry-run must redact secrets exactly as a real write does - never echo
        # a plaintext header value back in the preview.
        resp = self.client.post(
            f"/v1/watches/{self.watch_id}/actions?dry_run=true",
            {
                "kind": "webhook",
                "config": {"url": "https://hook.test/x", "headers": {"Authorization": "Bearer s3cr3t"}},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        blob = resp.json()["action"]["config"]
        self.assertNotIn("s3cr3t", str(blob))  # the plaintext secret is never echoed back
        self.assertEqual(blob["headers"]["Authorization"], "***")  # masked, like a real write

    def test_dry_run_edit_redacts_secrets(self) -> None:
        resp = self.client.put(
            f"/v1/actions/{self.action_id}?dry_run=true",  # log -> webhook carrying a secret
            {
                "kind": "webhook",
                "config": {"url": "https://hook.test/x", "headers": {"Authorization": "Bearer s3cr3t"}},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        blob = resp.json()["action"]["config"]
        self.assertNotIn("s3cr3t", str(blob))
        self.assertEqual(blob["headers"]["Authorization"], "***")


class WatchChainDryRunTests(TestCase):
    """The whole-watch create dry-run previews the action chain (built via the
    shared watch_action_input_wire -> watch_action_wire, retiring the old
    view-side _preview_action_wire) without persisting: each preview action has an
    empty id, and secrets are redacted just like a real write."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="chaindryrun@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_dry_run_previews_chain_without_persisting(self) -> None:
        resp = self.client.post(
            "/v1/watches?dry_run=true",
            {
                "name": "w",
                "feed_ids": [],
                "actions": [
                    {
                        "kind": "webhook",
                        "config": {"url": "https://hook.test/x", "headers": {"Authorization": "Bearer s3cr3t"}},
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["dry_run"])
        self.assertEqual(Watch.objects.count(), 0)  # nothing persisted
        action = data["actions"][0]
        self.assertEqual(action["id"], "")  # a preview chain action carries no id
        self.assertNotIn("s3cr3t", str(action))  # secret redacted on the preview chain too
        self.assertEqual(action["config"]["headers"]["Authorization"], "***")
