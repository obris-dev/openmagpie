"""An action kind's batch limits are optional: a plugin that implements the
Action protocol without subclassing it flushes a digest whole, and a limit
that would strand a window is refused when the kind registers."""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionRunState
from watches import tests_digest as digest
from watches.actions import registry as actions_registry
from watches.actions.protocol import BATCH_LIMITS, ActionResult


class _Structural:
    """An Action by shape only: a kind and a run, no batch limits."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.sizes: list[int] = []

    def run(self, action, *, items, context) -> ActionResult:
        self.sizes.append(len(items))
        return ActionResult(state=WatchActionRunState.SUCCEEDED)


class StructuralPluginFlushTests(TestCase):
    setUp = digest.DigestDeliveryTests.setUp
    _drain = digest.DigestDeliveryTests._drain
    _count = digest.DigestDeliveryTests._count
    _flush_due = digest.DigestDeliveryTests._flush_due
    _digest_window_with_items = digest.DigestDeliveryTests._digest_window_with_items

    def test_a_kind_without_batch_limits_flushes_its_window(self) -> None:
        now = timezone.now()
        _, action, window = self._digest_window_with_items(3, now=now)
        impl = _Structural(str(action.kind))
        with mock.patch.dict(actions_registry._REGISTRY, {str(action.kind): impl}):
            self._flush_due(window.close_at + timedelta(seconds=1))
        # FAILS (the window stuck) if a missing limit is read as an attribute.
        self.assertEqual(impl.sizes, [3])
        self.assertEqual(self._count(str(action.id), "succeeded"), 3)


class BatchLimitRegistrationTests(SimpleTestCase):
    def test_a_limit_that_would_strand_a_window_is_refused(self) -> None:
        for name in BATCH_LIMITS:
            for bad in (0, -1, True, "5"):
                impl = _Structural("plugin_kind")
                setattr(impl, name, bad)
                with (
                    self.subTest(name=name, limit=bad),
                    mock.patch.dict(actions_registry._REGISTRY, clear=False),
                    self.assertRaises(ValueError),
                ):
                    actions_registry.register(impl)

    def test_no_limit_or_a_positive_one_registers(self) -> None:
        for limit in (None, 1, 500):
            impl = _Structural("plugin_kind")
            for name in BATCH_LIMITS:
                setattr(impl, name, limit)
            with self.subTest(limit=limit), mock.patch.dict(actions_registry._REGISTRY, clear=False):
                actions_registry.register(impl)
                self.assertIs(actions_registry.get("plugin_kind"), impl)
