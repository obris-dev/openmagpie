"""A kind's byte budget sizes a digest slice: items past it stay pending and
go in the next slice of the same flush."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionRunState
from watches import tests_digest as digest
from watches.actions.log import LogAction
from watches.actions.protocol import ActionResult

# One test item's stored data, `{"title": "t0"}`, as JSON.
ITEM_BYTES = 15


class DigestByteBudgetTests(TestCase):
    setUp = digest.DigestDeliveryTests.setUp
    _drain = digest.DigestDeliveryTests._drain
    _count = digest.DigestDeliveryTests._count
    _flush_due = digest.DigestDeliveryTests._flush_due
    _digest_window_with_items = digest.DigestDeliveryTests._digest_window_with_items

    def _flush_sizes(self, n: int, *, max_bytes: int) -> tuple[list[int], str]:
        now = timezone.now()
        _, action, window = self._digest_window_with_items(n, now=now)
        sizes: list[int] = []

        def record(action, *, items, context):
            sizes.append(len(items))
            return ActionResult(state=WatchActionRunState.SUCCEEDED)

        with (
            mock.patch.object(LogAction, "max_batch_bytes", max_bytes, create=True),
            mock.patch.object(LogAction, "run", side_effect=record),
        ):
            self._flush_due(window.close_at + timedelta(seconds=1))
        return sizes, str(action.id)

    def test_a_slice_keeps_its_items_under_the_kind_s_byte_budget(self) -> None:
        sizes, action_id = self._flush_sizes(5, max_bytes=2 * ITEM_BYTES + 1)
        # FAILS if the slice ignores the byte budget (one run of 5).
        self.assertEqual(sizes, [2, 2, 1])
        self.assertEqual(self._count(action_id, "succeeded"), 5)

    def test_an_item_over_the_budget_still_goes_alone(self) -> None:
        sizes, action_id = self._flush_sizes(2, max_bytes=1)
        # FAILS if an oversized item stalls the window instead of going out.
        self.assertEqual(sizes, [1, 1])
        self.assertEqual(self._count(action_id, "succeeded"), 2)
