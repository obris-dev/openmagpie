"""A plugin (non-built-in) watch-action kind, registered end-to-end.

Proves the extensible-union + registry path: a fork registers a config class
(`watches.registry`) + an Action impl (`watches.actions.registry`) and the kind
validates, persists, renders (via the plugin fallback union member), and routes
through the execution registry, with zero core edits. The dummy kind here doubles
as the documented hook shape.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import ulid
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from pydantic import BaseModel, TypeAdapter, ValidationError
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from common.models import KIND_MAX_LENGTH
from openmagpie_schema.watch import WatchActionRunWire, build_watch_action_run_wire, build_watch_action_wire
from openmagpie_schema.watch_actions import WatchActionConfigBase, WatchActionConfigSummary
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import registry as config_registry
from watches.actions import registry as actions_registry
from watches.actions.protocol import Action, ActionContext, ActionItem, ActionResult
from watches.models import WatchAction

_KIND = "dummy_note"


class _DummyConfig(WatchActionConfigBase):
    """A minimal plugin config: one field + the read-path contract methods."""

    CONFIG_KIND = _KIND
    note: str = ""

    def redacted_dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        return WatchActionConfigSummary(detail=self.note)

    def merge_preserving(self, prior: WatchActionConfigBase) -> WatchActionConfigBase:
        return self


class _DummyAction(Action):
    kind = _KIND

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        return ActionResult(state=WatchActionRunState.SUCCEEDED, result={"seen": len(items)})


def _register() -> None:
    """The documented hook shape: one call registers the impl (execution routing)
    AND the typed config (validation/typing). A fork names this in
    OPENMAGPIE_PLUGIN_HOOKS."""
    actions_registry.register_action(_DummyAction(), _DummyConfig)


class RegistrySemanticsTests(SimpleTestCase):
    def test_register_makes_kind_known_and_routable(self) -> None:
        with (
            mock.patch.dict(config_registry._REGISTRY, clear=False),
            mock.patch.dict(actions_registry._REGISTRY, clear=False),
        ):
            _register()
            self.assertIn(_KIND, config_registry.known_kinds())
            self.assertIsInstance(config_registry.get_config_class(_KIND)(), _DummyConfig)
            self.assertEqual(actions_registry.get(_KIND).kind, _KIND)

    def test_empty_config_kind_rejected(self) -> None:
        class _NoKind(_DummyConfig):
            CONFIG_KIND = ""

        with self.assertRaises(ValueError):
            config_registry.register(_NoKind)

    def test_register_action_rejects_kind_mismatch(self) -> None:
        # The facade registers impl + config together and refuses a pair whose
        # kinds disagree (a class of bug the single call exists to prevent).
        class _OtherConfig(_DummyConfig):
            CONFIG_KIND = "other_kind"

        with self.assertRaises(ValueError):
            actions_registry.register_action(_DummyAction(), _OtherConfig)

    def test_register_rejects_builtin_kind(self) -> None:
        # A plugin can't silently reshape a core default: registering a built-in
        # kind raises on every entry point (config registry, impl registry, facade)
        # rather than overwriting the built-in.
        class _ClobberLog(_DummyConfig):
            CONFIG_KIND = "log"

        class _LogImpl(_DummyAction):
            kind = "log"

        with self.assertRaises(ValueError):
            config_registry.register(_ClobberLog)
        with self.assertRaises(ValueError):
            actions_registry.register(_LogImpl())
        with self.assertRaises(ValueError):
            actions_registry.register_action(_LogImpl(), _ClobberLog)

    def test_register_rejects_duplicate_plugin_kind(self) -> None:
        # Two plugins claiming the same kind is a collision (fail loud), not silent
        # last-wins; re-registering the identical class is idempotent. The impl + source
        # registries apply the identical guard (source one covered in the feeds suite).
        class _A(_DummyConfig):
            CONFIG_KIND = "dup_kind"

        class _B(_DummyConfig):
            CONFIG_KIND = "dup_kind"

        with mock.patch.dict(config_registry._REGISTRY, clear=False):
            config_registry.register(_A)
            config_registry.register(_A)  # same class -> idempotent, no raise
            with self.assertRaises(ValueError):
                config_registry.register(_B)

    def test_register_rejects_over_long_kind(self) -> None:
        # A kind longer than the WatchAction.kind column fails at registration (boot),
        # not with a write-time DataError.
        long_kind = "x" * (KIND_MAX_LENGTH + 1)

        class _LongConfig(_DummyConfig):
            CONFIG_KIND = long_kind

        class _LongImpl(_DummyAction):
            kind = long_kind

        with self.assertRaises(ValueError):
            config_registry.register(_LongConfig)
        with self.assertRaises(ValueError):
            actions_registry.register(_LongImpl())


class BuiltinActionKindInvariantTests(SimpleTestCase):
    """The collision guard keys off each registry's `_BUILTIN_KINDS`, built from the
    impls'/configs' own kind strings (`frozenset(_REGISTRY)`). Pin both to the
    WatchActionKind enum so a typo'd built-in `.kind`/`CONFIG_KIND` can't silently
    shrink the guarded set and let a plugin register a real built-in kind. Mirrors the
    source-side cross-check in feeds/tests_plugin_source_kinds.py."""

    def test_registries_builtin_kinds_match_enum(self) -> None:
        enum_kinds = frozenset(k.value for k in WatchActionKind)
        self.assertEqual(actions_registry._BUILTIN_KINDS, enum_kinds)
        self.assertEqual(config_registry._BUILTIN_KINDS, enum_kinds)


class _ScoreResult(BaseModel):
    score: int


class ResultEnforcementTests(SimpleTestCase):
    def test_enforce_result_validates_registered_schema(self) -> None:
        with mock.patch.dict(config_registry._RESULT_REGISTRY, clear=False):
            config_registry.register_result(_KIND, _ScoreResult)
            config_registry.enforce_result(_KIND, {"score": 1})  # conforming -> no raise
            with self.assertRaises(ValidationError):
                config_registry.enforce_result(_KIND, {"score": "nope"})

    def test_enforce_result_is_noop_for_unregistered_kind(self) -> None:
        config_registry.enforce_result("never_registered", {"anything": 1})  # no raise

    def test_register_action_result_arg_registers_the_schema(self) -> None:
        with (
            mock.patch.dict(config_registry._REGISTRY, clear=False),
            mock.patch.dict(actions_registry._REGISTRY, clear=False),
            mock.patch.dict(config_registry._RESULT_REGISTRY, clear=False),
        ):
            actions_registry.register_action(_DummyAction(), _DummyConfig, result=_ScoreResult)
            with self.assertRaises(ValidationError):
                config_registry.enforce_result(_KIND, {"score": "nope"})


class ResultSchemaDrainTests(SimpleTestCase):
    """The drain marks a SUCCEEDED run ERRORED if its result violates the kind's
    registered result schema (so a consumer can rely on the shape)."""

    def _op(self):
        from watches.models import WatchActionRun
        from watches.operations.drain import WatchDrainOperation

        run = WatchActionRun(id=ulid.ulid(), account_id=ulid.ulid(), kind=_KIND)
        return WatchDrainOperation(run, now=timezone.now())

    def test_nonconforming_result_is_errored_conforming_passes(self) -> None:
        op = self._op()
        action = WatchAction(kind=_KIND, config={}, rank=0)
        with mock.patch.dict(config_registry._RESULT_REGISTRY, clear=False):
            config_registry.register_result(_KIND, _ScoreResult)
            bad = ActionResult(state=WatchActionRunState.SUCCEEDED, result={"score": "x"})
            self.assertEqual(op._enforce_result_schema(action, bad).state, WatchActionRunState.ERRORED)
            good = ActionResult(state=WatchActionRunState.SUCCEEDED, result={"score": 5})
            self.assertEqual(op._enforce_result_schema(action, good).state, WatchActionRunState.SUCCEEDED)

    def test_unregistered_kind_passes_through_untouched(self) -> None:
        op = self._op()
        action = WatchAction(kind="never_registered", config={}, rank=0)
        good = ActionResult(state=WatchActionRunState.SUCCEEDED, result={"anything": 1})
        self.assertIs(op._enforce_result_schema(action, good), good)


class SharedResultEnforceTests(SimpleTestCase):
    """The shared enforcer backs BOTH the instant drain and the digest flush, so a
    digest plugin kind carries the same result-shape guarantee as an instant one. A
    violating result is marked ERRORED while an OutboundActionResult subtype (and its
    outbound record) is preserved, so a delivery that really POSTed still keeps its
    audit row."""

    def test_violating_outbound_result_keeps_subtype_and_outbound(self) -> None:
        from watches.actions.protocol import OutboundActionResult, OutboundCall
        from watches.operations.result_enforce import enforce_result_schema

        call = OutboundCall(target_host="x.test", method="POST", http_status=200, item_count=1, request_payload={})
        with mock.patch.dict(config_registry._RESULT_REGISTRY, clear=False):
            config_registry.register_result(_KIND, _ScoreResult)
            bad = OutboundActionResult(state=WatchActionRunState.SUCCEEDED, result={"score": "x"}, outbound=call)
            out = enforce_result_schema(_KIND, bad, label="r1")
            # subtype kept -> delivery row still written; assert (not assertIsInstance)
            # so the checker narrows `out` for the `.outbound` access below.
            assert isinstance(out, OutboundActionResult)
            self.assertIs(out.outbound, call)  # the real POST record preserved
            self.assertEqual(out.state, WatchActionRunState.ERRORED)

    def test_conforming_result_passes_through_unchanged(self) -> None:
        from watches.operations.result_enforce import enforce_result_schema

        with mock.patch.dict(config_registry._RESULT_REGISTRY, clear=False):
            config_registry.register_result(_KIND, _ScoreResult)
            good = ActionResult(state=WatchActionRunState.SUCCEEDED, result={"score": 5})
            self.assertIs(enforce_result_schema(_KIND, good, label="r1"), good)


class ExportedBaseFieldsTests(SimpleTestCase):
    """The base field classes are exported so a fork can define first-class typed
    wire/input/run members by subclassing them (the fuller federation path)."""

    def test_run_fields_base_is_subclassable(self) -> None:
        from typing import Literal

        from openmagpie_schema.watch import WatchActionRunFields

        class _R(BaseModel):
            score: int

        class _MatchRunWire(WatchActionRunFields):
            kind: Literal["dummy_note"] = "dummy_note"
            result: _R | None = None  # None on non-SUCCEEDED, like the built-ins

        wire = _MatchRunWire.model_validate(
            {
                "id": "1",
                "watch_id": "w",
                "action_id": "a",
                "feed_item_id": "f",
                "state": "succeeded",
                "result": {"score": 3},
            }
        )
        self.assertEqual(wire.kind, "dummy_note")
        assert wire.result is not None  # narrow for the type checker
        self.assertEqual(wire.result.score, 3)


class UnionFallbackTests(SimpleTestCase):
    def test_plugin_kind_renders_via_fallback_member(self) -> None:
        wire = build_watch_action_wire(kind=_KIND, rank=0, config={"note": "hi"})
        self.assertEqual(wire.kind, _KIND)
        self.assertEqual(wire.config, {"note": "hi"})  # untyped blob on the generic member

    def test_builtin_with_bad_config_raises_not_absorbed(self) -> None:
        # A built-in kind whose config can't type must fail the union (its typed
        # member fails and the fallback rejects built-in kinds) rather than sliding
        # into the fallback as a raw blob.
        with self.assertRaises(ValidationError):
            build_watch_action_wire(kind="log", rank=0, config={"prefix": {"not": "a string"}})

    def test_plugin_run_renders_via_fallback_member(self) -> None:
        run = build_watch_action_run_wire(
            kind=_KIND,
            id=ulid.ulid(),
            watch_id=ulid.ulid(),
            action_id=ulid.ulid(),
            feed_item_id=ulid.ulid(),
            state=WatchActionRunState.SUCCEEDED,
            result={"seen": 1},
        )
        self.assertEqual(run.kind, _KIND)
        self.assertEqual(run.result, {"seen": 1})

    def test_run_union_still_rejects_empty_kind(self) -> None:
        with self.assertRaises(ValidationError):
            TypeAdapter(WatchActionRunWire).validate_python({"kind": "", "id": "x"})

    def test_plugin_kind_rejects_whitespace_only_and_padded(self) -> None:
        # min_length=1 lets these through the field; the shared validator rejects a
        # whitespace-only kind AND a padded one (incl. a disguised built-in like
        # " log ", which must not slip past the built-in check into the fallback).
        for bad in ("   ", " custom ", " log "):
            with self.assertRaises(ValidationError):
                build_watch_action_wire(kind=bad, rank=0, config={})


class PluginActionHttpTests(TestCase):
    def setUp(self) -> None:
        self.user = SignupOperation(email="plugin-kind@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_create_and_render_a_plugin_action(self) -> None:
        with (
            mock.patch.dict(config_registry._REGISTRY, clear=False),
            mock.patch.dict(actions_registry._REGISTRY, clear=False),
        ):
            _register()
            resp = self.client.post(
                "/v1/watches",
                {"name": "w", "feed_ids": [], "actions": [{"kind": _KIND, "config": {"note": "hello"}}]},
                format="json",
            )
            self.assertEqual(resp.status_code, 201, resp.content)
            action = resp.json()["actions"][0]
            self.assertEqual(action["kind"], _KIND)
            self.assertEqual(action["config"], {"note": "hello"})

            # And it routes: the drain dispatches through the execution registry,
            # which now returns the plugin impl for this kind.
            self.assertIs(actions_registry.get(_KIND).__class__, _DummyAction)

    def test_unregistered_kind_is_rejected(self) -> None:
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "never_registered", "config": {}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
