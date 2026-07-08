"""Unit tests for the ambient once-a-day upgrade nudge (openmagpie/update_check.py).

Covers: quiet when the cache is fresh (no network), nudge when stale + behind, silence
when current / offline / opted-out / non-TTY / on the version+upgrade commands, and
that the check timestamp always advances. Stdlib unittest; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from openmagpie.config import UpdateCheck
from openmagpie.update_check import maybe_nudge, record

_MOD = "openmagpie.update_check"


def _cache(latest: str | None, *, age_hours: float) -> UpdateCheck:
    return UpdateCheck(last_checked_at=datetime.now(UTC) - timedelta(hours=age_hours), latest=latest)


class UpdateNudgeTests(unittest.TestCase):
    def setUp(self) -> None:
        # Default a clean, interactive, opted-in environment; individual tests override.
        tty = mock.patch(f"{_MOD}.sys")
        self.fake_sys = tty.start()
        self.fake_sys.stdout.isatty.return_value = True
        self.addCleanup(tty.stop)
        # Neutralize any MAGPIE_NO_UPDATE_CHECK inherited from the host shell.
        env = mock.patch.dict("os.environ", {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        import os

        os.environ.pop("MAGPIE_NO_UPDATE_CHECK", None)

    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version")
    @mock.patch(f"{_MOD}.load_update_check", return_value=_cache("0.7.0", age_hours=1))
    def test_fresh_cache_makes_no_network_call_and_stays_silent(self, _load, latest, save, console) -> None:
        maybe_nudge("feed")
        latest.assert_not_called()  # fresh -> no PyPI hit
        save.assert_not_called()
        console.hint.assert_not_called()

    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.load_update_check", return_value=_cache("0.7.0", age_hours=25))
    def test_stale_and_behind_nudges_and_restamps(self, _load, _latest, save, console) -> None:
        maybe_nudge("feed")
        console.hint.assert_called_once()
        self.assertIn("magpie upgrade", console.hint.call_args.args[0])
        self.assertIn("0.8.0", console.hint.call_args.args[0])
        save.assert_called_once()  # timestamp advanced

    @mock.patch(f"{_MOD}.__version__", "0.8.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.load_update_check", return_value=None)
    def test_stale_but_current_restamps_without_nudging(self, _load, _latest, save, console) -> None:
        maybe_nudge("feed")
        console.hint.assert_not_called()
        save.assert_called_once()

    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version", return_value=None)  # offline
    @mock.patch(f"{_MOD}.load_update_check", return_value=None)
    def test_offline_first_run_stays_silent_but_stamps(self, _load, _latest, save, console) -> None:
        maybe_nudge("feed")
        console.hint.assert_not_called()  # nothing known -> nothing to say
        save.assert_called_once()  # stamp so we don't re-hit the network next command

    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.latest_version")
    @mock.patch(f"{_MOD}.load_update_check")
    def test_opt_out_envs_skip_entirely(self, load, latest, console) -> None:
        # Our own switch AND the cross-tool DO_NOT_TRACK the telemetry system honors.
        for var in ("MAGPIE_NO_UPDATE_CHECK", "DO_NOT_TRACK"):
            with mock.patch.dict("os.environ", {var: "1"}):
                maybe_nudge("feed")
        load.assert_not_called()
        latest.assert_not_called()
        console.hint.assert_not_called()

    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.latest_version")
    @mock.patch(f"{_MOD}.load_update_check")
    def test_non_tty_skips(self, load, latest, console) -> None:
        self.fake_sys.stdout.isatty.return_value = False  # piped / redirected
        maybe_nudge("feed")
        load.assert_not_called()
        console.hint.assert_not_called()

    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.latest_version")
    @mock.patch(f"{_MOD}.load_update_check")
    def test_version_and_upgrade_commands_and_bare_invocation_skip(self, load, latest, console) -> None:
        for cmd in ("version", "upgrade", None):
            maybe_nudge(cmd)
        load.assert_not_called()
        latest.assert_not_called()
        console.hint.assert_not_called()

    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version", return_value=None)  # offline this run
    @mock.patch(f"{_MOD}.load_update_check", return_value=_cache("0.8.0", age_hours=25))
    def test_offline_but_previously_knew_behind_still_nudges(self, _load, _latest, save, console) -> None:
        # The refresh fails, but we already cached that 0.8.0 exists -> still nudge on
        # the carried-forward value, and restamp so we don't re-hit PyPI next command.
        maybe_nudge("feed")
        console.hint.assert_called_once()
        self.assertIn("0.8.0", console.hint.call_args.args[0])
        save.assert_called_once()

    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    @mock.patch(f"{_MOD}.save_update_check")
    @mock.patch(f"{_MOD}.latest_version", return_value=None)
    def test_naive_cached_timestamp_is_coerced_and_never_raises(self, _latest, _save, _console) -> None:
        # A config.json with a NAIVE last_checked_at (hand-edited / external) must be
        # coerced to aware on load so the on-close staleness compare can't TypeError.
        naive = UpdateCheck.model_validate({"last_checked_at": "2020-01-01T00:00:00", "latest": "0.8.0"})
        self.assertIsNotNone(naive.last_checked_at.tzinfo)  # validator stamped UTC
        with mock.patch(f"{_MOD}.load_update_check", return_value=naive):
            maybe_nudge("feed")  # must not raise (subtraction is aware - aware)


class RecordTests(unittest.TestCase):
    @mock.patch(f"{_MOD}.save_update_check")
    def test_caches_a_real_lookup_with_a_fresh_aware_timestamp(self, save) -> None:
        record("0.8.0")
        save.assert_called_once()
        saved = save.call_args.args[0]
        self.assertEqual(saved.latest, "0.8.0")
        self.assertIsNotNone(saved.last_checked_at.tzinfo)

    @mock.patch(f"{_MOD}.save_update_check")
    def test_failed_lookup_is_not_cached(self, save) -> None:
        record(None)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
