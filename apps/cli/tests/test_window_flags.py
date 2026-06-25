"""The run-window flag handling for `magpie activity export`: the SHARED resolver
(`resolve_window_value` / `resolve_run_windows` + `run_window_params`, which the
server invokes) and the CLI's `_build_windows` (forwards raw values, validates
client-side, applies the no-window default). Stdlib unittest; run:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import typer

from openmagpie.commands._shared import _build_windows
from openmagpie_schema.run_windows import (
    RUN_WINDOW_PARAMS,
    resolve_run_windows,
    resolve_window_value,
    run_window_params,
)


class ResolveWindowValueTests(unittest.TestCase):
    """The shared (server-side) resolver: a duration / ISO -> an absolute datetime."""

    def test_relative_duration_resolves_back_from_now(self) -> None:
        now = datetime(2026, 6, 25, tzinfo=UTC)
        self.assertEqual(resolve_window_value("7d", now=now), now - timedelta(days=7))

    def test_absolute_iso_naive_becomes_utc(self) -> None:
        parsed = resolve_window_value("2026-06-25", now=datetime.now(UTC))
        self.assertEqual(parsed.tzinfo, UTC)  # naive date read as UTC
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 6, 25))

    def test_garbage_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            resolve_window_value("last tuesday", now=datetime.now(UTC))

    def test_out_of_range_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):  # Feb 30: well-formed shape, invalid value
            resolve_window_value("2026-02-30T00:00:00Z", now=datetime.now(UTC))

    def test_huge_duration_raises_value_error_not_overflow(self) -> None:
        # The pattern allows unbounded digits; a giant duration overflows timedelta
        # (OverflowError) -- it must surface as ValueError, not escape as a 500.
        with self.assertRaises(ValueError):
            resolve_window_value("999999999999w", now=datetime.now(UTC))


class ResolveRunWindowsTests(unittest.TestCase):
    """The shared contract resolver: resolves all values + the two window guards."""

    def test_until_without_since_is_bounded_below(self) -> None:
        now = datetime(2026, 6, 25, tzinfo=UTC)
        out = resolve_run_windows({"occurred_until": "2026-06-10T00:00:00Z"}, now=now)
        self.assertIn("occurred_since", out)  # a lower bound was synthesized
        self.assertLess(out["occurred_since"], out["occurred_until"])

    def test_since_after_until_raises(self) -> None:
        with self.assertRaises(ValueError):  # inverted half-open window is empty
            resolve_run_windows(
                {"occurred_since": "2026-06-10T00:00:00Z", "occurred_until": "2026-06-01T00:00:00Z"},
                now=datetime.now(UTC),
            )

    def test_too_early_until_raises_not_overflow(self) -> None:
        # `until - SPAN` underflowing datetime.min must surface as ValueError (a 400),
        # not an uncaught OverflowError (a 500).
        with self.assertRaises(ValueError):
            resolve_run_windows({"occurred_until": "0001-01-04"}, now=datetime.now(UTC))


class BuildWindowsTests(unittest.TestCase):
    def test_no_flag_defaults_to_the_completed_window_raw(self) -> None:
        windows, defaulted = _build_windows()
        self.assertTrue(defaulted)  # a no-arg export must NOT scan all of retention
        self.assertEqual(windows, {"completed_since": "7d"})  # raw value; the server resolves it

    def test_explicit_flag_is_forwarded_raw(self) -> None:
        windows, defaulted = _build_windows(occurred_since="7d")
        self.assertFalse(defaulted)
        self.assertEqual(windows, {"occurred_since": "7d"})  # raw passthrough; no client-side resolution

    def test_bad_value_is_a_bad_parameter(self) -> None:
        with self.assertRaises(typer.BadParameter):  # validated client-side via the shared resolver
            _build_windows(occurred_since="last tuesday")

    def test_inverted_window_is_a_bad_parameter(self) -> None:
        with self.assertRaises(typer.BadParameter):  # ordering validated client-side too
            _build_windows(occurred_since="2026-06-10T00:00:00Z", occurred_until="2026-06-01T00:00:00Z")


class RunWindowParamsTests(unittest.TestCase):
    def test_drops_unset_and_keys_by_contract_name(self) -> None:
        self.assertEqual(
            run_window_params(occurred_since="a", completed_until="b"),
            {"occurred_since": "a", "completed_until": "b"},
        )

    def test_all_unset_is_empty(self) -> None:
        self.assertEqual(run_window_params(), {})

    def test_output_keys_match_the_contract_tuple(self) -> None:
        # by_name (value pairing) and RUN_WINDOW_PARAMS (the server's iteration
        # contract) must not desync -- a key in one but not the other silently drops.
        full = run_window_params(occurred_since="a", occurred_until="b", completed_since="c", completed_until="d")
        self.assertEqual(frozenset(full), frozenset(RUN_WINDOW_PARAMS))
