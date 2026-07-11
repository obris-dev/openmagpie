from typing import get_args

import ulid
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from openmagpie_schema.watch import WatchActionRunWire
from openmagpie_schema.watch_enums import WatchActionKind
from watches.models import WatchAction, WatchActionRun
from watches.registry import known_kinds


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

    def test_structurally_corrupt_kind_row_is_skipped(self) -> None:
        good = self._run(kind="log")
        # A whitespace-padded kind fails BOTH wire branches (the typed union has no such
        # literal; the plugin fallback rejects padding), a corrupt column: unbuildable
        # even with result=None, so the row is dropped rather than 500-ing the page.
        self._run(kind=" not a kind ")
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([i["id"] for i in resp.json()["items"]], [str(good.id)])

    def test_unregistered_plugin_kind_renders_via_fallback(self) -> None:
        # A plugin kind's run must render even when the plugin's hook ISN'T loaded (an
        # uninstall, or one replica missing the hooks env): PluginRunWire needs no
        # registration. This kind is registered in NEITHER registry, yet the row shows.
        # Regression for the old known_kinds() gate that hid a plugin's whole history.
        run = self._run(kind="unregistered_plugin_kind", result={"anything": 1})
        resp = self.client.get(f"/v1/actions/{self.action_id}/activity")
        self.assertEqual(resp.status_code, 200, resp.content)
        row = next(i for i in resp.json()["items"] if i["id"] == str(run.id))
        self.assertEqual(row["kind"], "unregistered_plugin_kind")
        self.assertEqual(row["result"], {"anything": 1})  # the blob renders as-is

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
    """`watch_action_run_wire` renders a run against the run union (no registry gate: a
    plugin run degrades safely via the PluginRunWire fallback, hook loaded or not). Two
    invariants keep that safe: (1) the run union's BUILT-IN tagged members are exactly
    the closed `WatchActionKind` enum, so every built-in kind renders as its TYPED member
    (not the open fallback, which would drop its typed result shape); (2) every built-in
    enum kind is registered, so `set(WatchActionKind)` is a SUBSET of known_kinds()
    (plugins add more), keeping the built-in write/drain path whole."""

    def test_builtin_run_union_members_match_enum_and_enum_subset_of_known(self) -> None:
        # The run union is now Annotated[_Builtin | PluginRunWire, left_to_right],
        # so unwrapping to the tagged built-in members takes one extra layer than
        # before. The plugin fallback (kind: str, no Literal default) is excluded.
        outer_members = get_args(get_args(WatchActionRunWire)[0])  # (_Builtin annotated, PluginRunWire)
        builtin_union = get_args(outer_members[0])[0]  # the discriminated Union of tagged members
        run_members = get_args(builtin_union)
        run_union_kinds = {str(member.model_fields["kind"].default) for member in run_members}
        enum_kinds = {str(kind) for kind in WatchActionKind}
        self.assertEqual(run_union_kinds, enum_kinds)
        self.assertLessEqual(enum_kinds, known_kinds())  # every built-in kind is registered

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
