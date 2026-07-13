"""process_due_runs --concurrency: the thread-pool drain plumbing.

Unit level (no DB, no engine): a fake claimer yields fake runs and
WatchDrainOperation is mocked, so these pin the pool contract independent of the
rest of the stack. Every claimed run is drained exactly once ; a run whose drain
raises comes back as (None, exc) instead of aborting the pass ; a lost claim (None
outcome) passes through ; and no more than N judges are ever in flight at once.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest import mock

from django.test import SimpleTestCase

from watches.management.commands.process_due_runs import Command
from watches.models import WatchActionRun

_DRAIN = "watches.management.commands.process_due_runs.WatchDrainOperation"
_CONNECTIONS = "watches.management.commands.process_due_runs.connections"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(rid: str) -> WatchActionRun:
    # A stand-in the mocked drain never persists ; cast so the typed helpers accept it.
    return cast(WatchActionRun, SimpleNamespace(id=rid, action_id="a"))


def _outcome(state: str = "gated") -> mock.MagicMock:
    return mock.MagicMock(state=mock.MagicMock(value=state))


class SafeDrainTests(SimpleTestCase):
    def test_returns_outcome_on_success(self) -> None:
        with mock.patch(_DRAIN) as drain:
            drain.return_value.run.return_value = _outcome("succeeded")
            outcome, exc = Command()._safe_drain(_run("r1"), _NOW)
        self.assertIsNone(exc)
        assert outcome is not None
        self.assertEqual(outcome.state.value, "succeeded")

    def test_captures_exception_instead_of_raising(self) -> None:
        with mock.patch(_DRAIN) as drain:
            drain.return_value.run.side_effect = RuntimeError("boom")
            outcome, exc = Command()._safe_drain(_run("r1"), _NOW)
        self.assertIsNone(outcome)
        self.assertIsInstance(exc, RuntimeError)

    def test_lost_claim_none_passes_through(self) -> None:
        with mock.patch(_DRAIN) as drain:
            drain.return_value.run.return_value = None  # claim lost mid-judge
            outcome, exc = Command()._safe_drain(_run("r1"), _NOW)
        self.assertIsNone(outcome)
        self.assertIsNone(exc)


class ConcurrentCompletionsTests(SimpleTestCase):
    def test_drains_every_run_once_mixing_outcomes(self) -> None:
        runs = [_run(f"r{i}") for i in range(20)]

        def make_op(run: WatchActionRun, now: datetime | None = None) -> mock.MagicMock:
            op = mock.MagicMock()
            n = int(str(run.id)[1:])
            if n % 5 == 0:
                op.run.side_effect = RuntimeError("boom")  # infra failure
            elif n % 5 == 1:
                op.run.return_value = None  # lost claim
            else:
                op.run.return_value = _outcome("gated")  # normal
            return op

        with mock.patch(_DRAIN, side_effect=make_op):
            results = list(Command()._concurrent_completions(iter(runs), _NOW, 4))

        self.assertEqual(sorted(r.id for r, _ in results), sorted(r.id for r in runs))
        self.assertTrue(any(exc is not None for _, (_o, exc) in results))  # failures captured
        self.assertTrue(any(o is None and exc is None for _, (o, exc) in results))  # lost claims
        self.assertTrue(any(o is not None for _, (o, _e) in results))  # normal outcomes

    def test_claims_at_most_n_ahead_of_consumption(self) -> None:
        # The load-bearing property of fill() is the CLAIM bound: at most N runs are
        # pulled from the claimer (each pull is the CAS flip to RUNNING) ahead of
        # results handed back. A ThreadPoolExecutor(max_workers=N) already caps
        # EXECUTION, so a test that only counts concurrent judges would still pass if
        # fill() claimed the whole backlog up front; assert on the claim side instead.
        total, n = 20, 4
        pulled = 0

        def claimer() -> Iterator[WatchActionRun]:
            nonlocal pulled
            for i in range(total):
                pulled += 1  # a pull == a CAS claim; only the main thread does this
                yield _run(f"r{i}")

        def make_op(run: WatchActionRun, now: datetime | None = None) -> mock.MagicMock:
            op = mock.MagicMock()

            def run_fn() -> mock.MagicMock:
                time.sleep(0.003)  # keep judges in flight so fill() must hold the bound
                return _outcome("gated")

            op.run.side_effect = run_fn
            return op

        max_ahead, consumed = 0, 0
        with mock.patch(_DRAIN, side_effect=make_op):
            for _run_result in Command()._concurrent_completions(claimer(), _NOW, n):
                consumed += 1
                max_ahead = max(max_ahead, pulled - consumed)

        self.assertEqual(consumed, total)
        self.assertLessEqual(max_ahead, n)  # never more than N claimed ahead of drained


class ConnectionHygieneTests(SimpleTestCase):
    """The serial path runs _safe_drain on the MAIN thread while it iterates
    claim_due's server-side cursor, so _safe_drain must never close that connection
    (CONN_MAX_AGE would otherwise break the cursor's next fetch). Closing lives in
    _drain_worker, which only the pool threads run (regression guard: a close in
    _safe_drain crashed every serial pass past 60s at the next chunk fetch)."""

    def test_safe_drain_never_touches_connections(self) -> None:
        with mock.patch(_DRAIN) as drain, mock.patch(_CONNECTIONS) as conns:
            drain.return_value.run.return_value = _outcome("gated")
            Command()._safe_drain(_run("r1"), _NOW)
        conns.close_all.assert_not_called()

    def test_drain_worker_closes_its_connections(self) -> None:
        with mock.patch(_DRAIN) as drain, mock.patch(_CONNECTIONS) as conns:
            drain.return_value.run.return_value = _outcome("gated")
            outcome, exc = Command()._drain_worker(_run("r1"), _NOW)
        self.assertIsNone(exc)
        assert outcome is not None
        self.assertEqual(outcome.state.value, "gated")
        conns.close_all.assert_called_once()
