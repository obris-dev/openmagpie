"""Unit tests for `magpie feed source seek` (move the polling watermark of one
source by own id, or of every source on a feed via --feed).

Stdlib unittest. Run with:
  uv run --no-sync python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest import mock

import typer

from openmagpie.api.feed import SourceSeekResult

_MOD = "openmagpie.commands.feed._source_seek"
_PREVIEW_TO = datetime(2026, 10, 12, tzinfo=UTC)


def _result(*, advanced: int = 1, rewound: int = 0, unchanged: int = 0, dry_run: bool) -> SourceSeekResult:
    return SourceSeekResult(
        advanced=advanced,
        rewound=rewound,
        unchanged=unchanged,
        source_count=advanced + rewound + unchanged,
        to=_PREVIEW_TO,
        dry_run=dry_run,
    )


def _run(**kwargs):
    """Call the command function directly with every parameter explicit (typer's
    Option/Argument defaults are sentinels when bypassing the CLI parser)."""
    from openmagpie.commands.feed._source_seek import seek

    params = {"source_id": None, "feed_id": None, "to": "now", "dry_run": False, "yes": False}
    params.update(kwargs)
    return seek(**params)


@mock.patch(f"{_MOD}.console")
@mock.patch(f"{_MOD}.app_ctx")
class SeekCommandTests(unittest.TestCase):
    def _ac(self, app_ctx: mock.Mock) -> mock.Mock:
        ac = mock.Mock()
        app_ctx.return_value = ac
        # The apply returns a DIFFERENT tally from the preview, so a test can tell
        # which response a line was rendered from (the `moved` line must be the
        # apply's: it is the authoritative count).
        ac.api.feed.seek_source.side_effect = lambda sid, *, to, dry_run: (
            _result(dry_run=True) if dry_run else _result(advanced=2, dry_run=False)
        )
        ac.api.feed.seek_sources.side_effect = lambda fid, *, to, dry_run: (
            _result(dry_run=True) if dry_run else _result(advanced=2, dry_run=False)
        )
        return ac

    def test_by_id_previews_then_applies(self, app_ctx, console) -> None:
        ac = self._ac(app_ctx)
        _run(source_id="SID", yes=True)
        # Both calls carry the RAW --to: the server resolves it on one clock read
        # inside each request (pinning the preview's instant froze a target the
        # server's moving retention floor then rejected on apply).
        self.assertEqual(
            ac.api.feed.seek_source.call_args_list,
            [mock.call("SID", to="now", dry_run=True), mock.call("SID", to="now", dry_run=False)],
        )
        ac.api.feed.seek_sources.assert_not_called()
        # An apply prints only the `moved` line (the silent preview sizes the gate),
        # matching `feed source set`'s did:/would: split; `would move` is the --dry-run output.
        lines = [c.args[0] for c in console.success.call_args_list]
        self.assertEqual(len(lines), 1)
        self.assertTrue(
            lines[0].startswith("moved 2 of 2 source(s) to "), lines
        )  # the APPLY's tally, not the preview's
        self.assertTrue(lines[0].endswith(": advanced=2 | rewound=0 | unchanged=0"), lines)

    def test_feed_scope_uses_the_collection_route(self, app_ctx, _console) -> None:
        ac = self._ac(app_ctx)
        _run(feed_id="FID", to="yesterday", yes=True)  # raw keyword forwarded, not pre-resolved
        self.assertEqual(
            ac.api.feed.seek_sources.call_args_list,
            [mock.call("FID", to="yesterday", dry_run=True), mock.call("FID", to="yesterday", dry_run=False)],
        )
        ac.api.feed.seek_source.assert_not_called()

    def test_oldest_and_a_duration_pass_local_validation_and_go_raw(self, app_ctx, _console) -> None:
        # `oldest` needs a floor only the server has (the local check stands one
        # in); like every target it goes to the server raw on both calls.
        ac = self._ac(app_ctx)
        for raw in ("Oldest", "7d"):
            with self.subTest(raw=raw):
                ac.api.feed.seek_sources.reset_mock()
                _run(feed_id="FID", to=raw, yes=True)
                self.assertEqual(
                    ac.api.feed.seek_sources.call_args_list,
                    [mock.call("FID", to=raw, dry_run=True), mock.call("FID", to=raw, dry_run=False)],
                )
        self.assertEqual(
            ac.api.feed.seek_sources.call_args_list,
            [mock.call("FID", to="7d", dry_run=True), mock.call("FID", to="7d", dry_run=False)],
        )

    def test_both_or_neither_target_is_a_usage_error(self, app_ctx, _console) -> None:
        ac = self._ac(app_ctx)
        # Same usage-error type (and exit code 2) as a bad --to.
        for kwargs in ({}, {"source_id": "SID", "feed_id": "FID"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(typer.BadParameter):
                _run(**kwargs)
        ac.api.feed.seek_source.assert_not_called()
        ac.api.feed.seek_sources.assert_not_called()

    def test_dry_run_makes_exactly_one_preview_call(self, app_ctx, console) -> None:
        ac = self._ac(app_ctx)
        _run(source_id="SID", dry_run=True)
        ac.api.feed.seek_source.assert_called_once_with("SID", to="now", dry_run=True)
        lines = [c.args[0] for c in console.success.call_args_list]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("would move 1 of 1 source(s) to "), lines)
        self.assertTrue(lines[0].endswith(": advanced=1 | rewound=0 | unchanged=0"), lines)

    def test_nothing_to_do_skips_the_apply(self, app_ctx, console) -> None:
        ac = self._ac(app_ctx)
        ac.api.feed.seek_sources.side_effect = lambda fid, *, to, dry_run: _result(
            advanced=0, rewound=0, unchanged=3, dry_run=dry_run
        )
        _run(feed_id="FID", yes=True)
        ac.api.feed.seek_sources.assert_called_once_with("FID", to="now", dry_run=True)
        self.assertIn("already at that watermark", console.warn.call_args.args[0])

    def test_empty_feed_says_so_even_under_dry_run(self, app_ctx, console) -> None:
        ac = self._ac(app_ctx)
        ac.api.feed.seek_sources.side_effect = lambda fid, *, to, dry_run: _result(
            advanced=0, rewound=0, unchanged=0, dry_run=dry_run
        )
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                console.reset_mock()
                ac.api.feed.seek_sources.reset_mock()
                _run(feed_id="FID", yes=True, dry_run=dry_run)
                ac.api.feed.seek_sources.assert_called_once_with("FID", to="now", dry_run=True)
                self.assertEqual(console.warn.call_args.args[0], "No sources on this feed.")
                console.success.assert_not_called()  # no `would move 0 of 0` line

    def test_preview_not_honoured_aborts_before_the_prompt(self, app_ctx, _console) -> None:
        # Version skew: a server that ignores dry_run would have WRITTEN on the
        # "preview". The echo exposes that; the command must stop, not prompt.
        ac = self._ac(app_ctx)
        ac.api.feed.seek_source.side_effect = lambda sid, *, to, dry_run: _result(dry_run=False)
        with self.assertRaises(typer.Exit) as ctx:
            _run(source_id="SID", yes=True)
        self.assertEqual(ctx.exception.exit_code, 1)
        ac.api.feed.seek_source.assert_called_once_with("SID", to="now", dry_run=True)  # no apply
        # ...and the operator is told what was written, so they can undo it.
        applied = [c.args[0] for c in _console.error.call_args_list if "APPLIED" in c.args[0]]
        self.assertEqual(len(applied), 1, _console.error.call_args_list)
        self.assertIn("moved 1 of 1 source(s) to ", applied[0])

    def test_bad_to_fails_locally_before_any_call(self, app_ctx, _console) -> None:
        ac = self._ac(app_ctx)
        with self.assertRaises(typer.BadParameter):
            _run(source_id="SID", to="last tuesday", yes=True)
        ac.api.feed.seek_source.assert_not_called()

    def test_piped_input_without_yes_refuses_to_apply(self, app_ctx, _console) -> None:
        ac = self._ac(app_ctx)
        with mock.patch(f"{_MOD}.sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(typer.Exit):
                _run(source_id="SID")
        ac.api.feed.seek_source.assert_called_once_with("SID", to="now", dry_run=True)  # preview only


if __name__ == "__main__":
    unittest.main()
