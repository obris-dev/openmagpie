"""Unit tests for `magpie upgrade` (CLI self-update).

Covers the version compare, the manager detection gate, the --check / --yes / piped
paths, and that it never shells out unless it should. Stdlib unittest; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

import typer

from openmagpie.commands.upgrade import _detect_manager, _upgrade_argv, upgrade

_MOD = "openmagpie.commands.upgrade"


class UpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        # upgrade() records the PyPI lookup into the update-check cache; no-op that
        # write in tests so it doesn't touch the real ~/.magpie/config.json.
        p = mock.patch(f"{_MOD}.record")
        p.start()
        self.addCleanup(p.stop)

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.7.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_already_latest_does_nothing(self, _console, _latest, run) -> None:
        upgrade(check=False, yes=True)
        run.assert_not_called()

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}._detect_manager", return_value="uv")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_newer_upgrades_via_detected_manager(self, _console, _latest, _mgr, run) -> None:
        upgrade(check=False, yes=True)  # --yes skips the confirm
        run.assert_called_once()
        argv = run.call_args.args[0]
        self.assertEqual(argv, ["uv", "tool", "install", "--force", "openmagpie"])

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}._detect_manager", return_value="uv")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_check_reports_without_upgrading(self, _console, _latest, _mgr, run) -> None:
        upgrade(check=True, yes=False)
        run.assert_not_called()

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}.sys")
    @mock.patch(f"{_MOD}._detect_manager", return_value="uv")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_piped_without_yes_aborts(self, _console, _latest, _mgr, fake_sys, run) -> None:
        fake_sys.stdin.isatty.return_value = False  # piped: can't prompt
        with self.assertRaises(typer.Exit):
            upgrade(check=False, yes=False)
        run.assert_not_called()

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}._detect_manager", return_value=None)
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_undetected_manager_advises_and_exits(self, _console, _latest, _mgr, run) -> None:
        with self.assertRaises(typer.Exit):
            upgrade(check=False, yes=True)
        run.assert_not_called()  # never guess-mutate with the wrong tool

    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}.latest_version", return_value=None)  # lookup failed (offline / bad shape)
    @mock.patch(f"{_MOD}.console")
    def test_pypi_unreachable_errors(self, _console, _latest, run) -> None:
        with self.assertRaises(typer.Exit):
            upgrade(check=False, yes=True)
        run.assert_not_called()

    @mock.patch(f"{_MOD}.typer.confirm", return_value=False)
    @mock.patch(f"{_MOD}.sys")
    @mock.patch(f"{_MOD}.subprocess.run")
    @mock.patch(f"{_MOD}._detect_manager", return_value="uv")
    @mock.patch(f"{_MOD}.latest_version", return_value="0.8.0")
    @mock.patch(f"{_MOD}.__version__", "0.7.0")
    @mock.patch(f"{_MOD}.console")
    def test_interactive_decline_does_not_upgrade(self, _console, _latest, _mgr, run, fake_sys, _confirm) -> None:
        fake_sys.stdin.isatty.return_value = True  # interactive; confirm() -> False
        with self.assertRaises(typer.Exit):
            upgrade(check=False, yes=False)
        run.assert_not_called()


class DetectManagerTests(unittest.TestCase):
    @mock.patch(f"{_MOD}.shutil.which", return_value=None)  # neither uv nor pipx on PATH
    @mock.patch(f"{_MOD}.subprocess.run")
    def test_pip_detected_when_isolated_tools_absent(self, run, _which) -> None:
        run.return_value = mock.Mock(returncode=0)  # `pip show openmagpie` succeeds here
        self.assertEqual(_detect_manager(), "pip")

    @mock.patch(f"{_MOD}.shutil.which", return_value=None)
    @mock.patch(f"{_MOD}.subprocess.run")
    def test_none_when_not_pip_installed_here(self, run, _which) -> None:
        run.return_value = mock.Mock(returncode=1)  # pip show fails -> unknown installer
        self.assertIsNone(_detect_manager())

    @mock.patch(f"{_MOD}.shutil.which", side_effect=lambda t: "/bin/uv" if t == "uv" else None)
    @mock.patch(f"{_MOD}.subprocess.run")
    def test_line_anchor_rejects_prefix_sharing_sibling(self, run, _which) -> None:
        # `uv tool list` shows only openmagpie-extras (a prefix sibling), not openmagpie;
        # the first-token anchor must NOT match it, and pip-show (rc 1) then rules out pip.
        run.return_value = mock.Mock(returncode=1, stdout="openmagpie-extras v1.0.0\n")
        self.assertIsNone(_detect_manager())


class UpgradeArgvTests(unittest.TestCase):
    def test_pip_runs_in_this_interpreter(self) -> None:
        self.assertEqual(_upgrade_argv("pip"), [sys.executable, "-m", "pip", "install", "--upgrade", "openmagpie"])

    def test_uv_and_pipx_force_reinstall(self) -> None:
        self.assertEqual(_upgrade_argv("uv"), ["uv", "tool", "install", "--force", "openmagpie"])
        self.assertEqual(_upgrade_argv("pipx"), ["pipx", "install", "--force", "openmagpie"])


if __name__ == "__main__":
    unittest.main()
