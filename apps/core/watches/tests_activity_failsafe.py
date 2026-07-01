from typing import get_args

import ulid
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from openmagpie_schema.watch import WatchActionRunWire
from openmagpie_schema.watch_enums import WatchActionKind
from watches.models import WatchAction, WatchActionRun
from watches.registry import KNOWN_KINDS


class ActionActivityFailSafeTests(TestCase):
    """The activity LIST must never 500 on one bad row. A run whose stored
    `state` or `kind` can't be typed is skipped (the page still returns the good
    rows); a malformed `result` degrades to null while the row still renders its
    state + ids. Regression guard for the per-row fail-safe in
    `watch_action_run_wire` (state + kind validated inside it, narrow catches)."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="failsafe@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id

    def _run(self, *, kind: str = "log", state: str = "succeeded", result=None) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            kind=kind,
            feed_item_id=ulid.ulid(),
            state=state,
            result=result or {},
            scheduled_at=timezone.now(),
        )

    def test_bad_state_row_is_skipped_not_500(self) -> None:
        good = self._run(state="succeeded")
        self._run(state="bogus_state")  # legacy/degenerate; can't coerce to the enum
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)  # the whole page must not 500
        self.assertEqual([i["id"] for i in resp.json()["items"]], [str(good.id)])

    def test_unrenderable_kind_row_is_skipped(self) -> None:
        good = self._run(kind="log")
        self._run(kind="not_a_kind")  # no union member -> unbuildable, dropped
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([i["id"] for i in resp.json()["items"]], [str(good.id)])

    def test_malformed_result_degrades_to_null(self) -> None:
        # A semantic_filter result needs a numeric `score` + `passed`; a degenerate
        # stored value can't type, so the row keeps its state + ids with result=null
        # rather than dropping (a valid kind, only the result is bad).
        run = self._run(kind="semantic_filter", state="succeeded", result={"score": "not-a-number"})
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        row = next(i for i in resp.json()["items"] if i["id"] == str(run.id))
        self.assertIsNone(row["result"])
        self.assertEqual(row["state"], "succeeded")


class RunUnionKindInvariantTests(SimpleTestCase):
    """`watch_action_run_wire` gates a run on KNOWN_KINDS (the config registry),
    then does an UNGUARDED rebuild against the run union's members. That is only
    safe while the two sets coincide: a kind in the registry with no run-union
    member would pass the gate and then 500 the page on the unguarded rebuild.
    Enforce the invariant so a future kind can't drift the two apart silently."""

    def test_known_kinds_equal_run_union_members_equal_enum(self) -> None:
        # Derive the run union's discriminators from the union itself (not a
        # hardcoded list), so a new member is picked up automatically.
        run_members = get_args(get_args(WatchActionRunWire)[0])
        run_union_kinds = {str(member.model_fields["kind"].default) for member in run_members}
        enum_kinds = {str(kind) for kind in WatchActionKind}
        self.assertEqual(run_union_kinds, enum_kinds)
        self.assertEqual({str(kind) for kind in KNOWN_KINDS}, enum_kinds)

    def test_result_models_have_fields_so_a_real_dump_is_never_empty(self) -> None:
        """`build_watch_action_run_wire` coalesces an empty result dict ({}) to
        None, so a REAL result dump must never be {} (else a real result would
        read as 'no result'). model_dump keys off every field, so this holds iff
        each result model has at least one field. Pin it."""
        from openmagpie_schema.watch_actions import (
            ExtractResult,
            LogResult,
            SemanticFilterResult,
            WebhookResult,
        )

        for model in (SemanticFilterResult, ExtractResult, LogResult, WebhookResult):
            self.assertTrue(model.model_fields, f"{model.__name__} has no fields; its dump would be {{}}")
