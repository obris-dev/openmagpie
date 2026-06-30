"""notice_after_login: the post-login opt-out disclosure.

Opt-out collects for the whole instance, so EVERY user is disclosed to. Owners get
the off switch; members (who can't change the account setting: it 403s, and the
server's DO_NOT_TRACK is out of their reach) get a 'managed by your owner' pointer
instead, never the `telemetry disable` verb. Both are marked disclosed so a login
isn't a repeated API no-op.

Stdlib unittest. Run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

from openmagpie.commands.telemetry import notice_after_login, status
from openmagpie.config import Config
from openmagpie_schema.telemetry import TelemetryState


def _run_notice(
    *, can_set: bool, mode: str = "unset", disclosed: bool = False, emitting: bool | None = True
) -> tuple[Config, str]:
    cfg = Config()
    cfg.telemetry_disclosed = disclosed
    ac = mock.Mock()
    ac.config = cfg
    ac.api.telemetry.get.return_value = TelemetryState(mode=mode, can_set=can_set, emitting=emitting)
    with (
        mock.patch("openmagpie.commands.telemetry.save"),
        mock.patch("sys.stdin") as stdin,
        mock.patch("openmagpie.commands.telemetry.console") as console,
    ):
        stdin.isatty.return_value = True
        notice_after_login(ac)
    text = " ".join(str(c.args[0]) for c in console.log.call_args_list if c.args)
    return cfg, text


class NoticeAfterLoginTests(unittest.TestCase):
    def test_owner_gets_the_off_switch_and_is_marked(self) -> None:
        cfg, text = _run_notice(can_set=True)
        self.assertIn("magpie telemetry disable", text)
        self.assertTrue(cfg.telemetry_disclosed)

    def test_member_gets_owner_pointer_not_the_403_verb_and_is_marked(self) -> None:
        cfg, text = _run_notice(can_set=False)
        self.assertNotIn("telemetry disable", text)  # 403s for a member; must not be shown
        self.assertIn("managed by your account owner", text)
        # marked even for a member, so a non-owner login isn't a repeated API no-op
        self.assertTrue(cfg.telemetry_disclosed)

    def test_no_disclosure_when_server_reports_not_emitting(self) -> None:
        # unset but suppressed server-side (DO_NOT_TRACK / no key): nothing is being
        # sent, so don't over-disclose "it's on". NOT marked: if suppression is later
        # lifted (emission turns on), a future login must still fire the one-time notice.
        cfg, text = _run_notice(can_set=True, emitting=False)
        self.assertEqual(text, "")
        self.assertFalse(cfg.telemetry_disclosed)

    def test_no_disclosure_against_old_server(self) -> None:
        # emitting=None: server too old to report it (pre-opt-out, where unset is silent).
        # Don't claim "on" on that skew, and don't mark: when the server upgrades to
        # opt-out (emission on), a later login should still disclose.
        cfg, text = _run_notice(can_set=True, emitting=None)
        self.assertEqual(text, "")
        self.assertFalse(cfg.telemetry_disclosed)

    def test_skips_api_when_already_disclosed(self) -> None:
        cfg = Config()
        cfg.telemetry_disclosed = True
        ac = mock.Mock()
        ac.config = cfg
        with mock.patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            notice_after_login(ac)
        ac.api.telemetry.get.assert_not_called()  # no round-trip once disclosed


class StatusRenderTests(unittest.TestCase):
    """`telemetry status` is the surface TELEMETRY.md calls the universal way to check
    'is it on', so it must render the server-computed `emitting` -- `mode` alone is
    ambiguous (opt-out: `unset` means ON)."""

    def _run_status(self, *, mode: str = "unset", emitting: bool | None = True) -> str:
        ac = mock.Mock()
        ac.api.telemetry.get.return_value = TelemetryState(mode=mode, can_set=True, emitting=emitting)
        with (
            mock.patch("openmagpie.commands.telemetry.app_ctx", return_value=ac),
            mock.patch("openmagpie.commands.telemetry.console") as console,
        ):
            status()
        return " ".join(str(c.args[0]) for c in console.log.call_args_list if c.args)

    def test_unset_default_shows_emitting_yes(self) -> None:
        text = self._run_status(mode="unset", emitting=True)
        self.assertIn("unset", text)
        self.assertRegex(text, r"[Ee]mitting.*yes")  # opt-out: unset means on-and-sending

    def test_emitting_line_omitted_when_server_silent(self) -> None:
        text = self._run_status(emitting=None)  # older server didn't report it
        self.assertNotIn("Emitting", text)


if __name__ == "__main__":
    unittest.main()
