"""Unit tests for `magpie backfill submit` / `status` / `list`.

Client-side validation (window required), the flag->client wiring (raw window
values forwarded), the dry-run-honored guard, and the --replace confirm gate.
Stdlib unittest; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer

from openmagpie.commands.backfill import backfill_list, backfill_status, backfill_submit
from openmagpie_schema.backfill import BackfillJob, BackfillListResponse, BackfillPreview


def _preview(**kw) -> BackfillPreview:
    kw.setdefault("dry_run", True)  # server-honored marker; the command guards on it
    return BackfillPreview(matched=5, present=4, pruned=1, would_delete=2, would_enqueue=4, **kw)


def _job(**kw) -> BackfillJob:
    kw.setdefault("state", "pending")
    return BackfillJob(id="01JOB", target_action_id="01TGT", **kw)


class BackfillValidationTests(unittest.TestCase):
    """Validation fires before any API call (no app_ctx needed)."""

    def test_no_window_raises_bad_parameter(self) -> None:
        with self.assertRaises(typer.BadParameter):
            backfill_submit(
                "01TGT",
                occurred_since=None,
                occurred_until=None,
                completed_since=None,
                completed_until=None,
                replace=False,
                dry_run=False,
                yes=True,
            )

    def test_bad_duration_raises_bad_parameter(self) -> None:
        with self.assertRaises(typer.BadParameter):
            backfill_submit(
                "01TGT",
                occurred_since="nonsense",
                occurred_until=None,
                completed_since=None,
                completed_until=None,
                replace=False,
                dry_run=False,
                yes=True,
            )


class BackfillWiringTests(unittest.TestCase):
    """Flags -> client call (raw window values forwarded; dry_run routed)."""

    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_dry_run_calls_client_with_preview(self, app_ctx, _console) -> None:
        ac = mock.Mock()
        ac.api.watch.preview_backfill.return_value = _preview(replace=True)
        app_ctx.return_value = ac
        backfill_submit(
            "01TGT",
            occurred_since="7d",
            occurred_until=None,
            completed_since=None,
            completed_until=None,
            replace=True,
            dry_run=True,
            yes=False,
        )
        ac.api.watch.preview_backfill.assert_called_once_with("01TGT", replace=True, windows={"occurred_since": "7d"})
        ac.api.watch.submit_backfill.assert_not_called()  # --dry-run previews only, never queues

    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_submit_forwards_windows_and_flags(self, app_ctx, _console) -> None:
        ac = mock.Mock()
        ac.api.watch.submit_backfill.return_value = _job(replace=True)
        app_ctx.return_value = ac
        backfill_submit(
            "01TGT",
            occurred_since=None,
            occurred_until=None,
            completed_since="30d",
            completed_until="1d",
            replace=True,
            dry_run=False,
            yes=True,  # yes skips the --replace confirm
        )
        ac.api.watch.submit_backfill.assert_called_once_with(
            "01TGT", replace=True, windows={"completed_since": "30d", "completed_until": "1d"}
        )

    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_replace_without_yes_aborts_when_piped(self, app_ctx, _console, fake_sys) -> None:
        fake_sys.stdin.isatty.return_value = False  # piped: can't prompt
        app_ctx.return_value = mock.Mock()
        with self.assertRaises(typer.Exit):
            backfill_submit(
                "01TGT",
                occurred_since=None,
                occurred_until=None,
                completed_since="30d",
                completed_until=None,
                replace=True,
                dry_run=False,
                yes=False,
            )
        app_ctx.return_value.api.watch.submit_backfill.assert_not_called()

    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_additive_piped_without_yes_aborts(self, app_ctx, _console, fake_sys) -> None:
        # Even additive (non-replace) submit is a mutation (enqueues LLM-cost runs);
        # a pipe without --yes must not silently trigger it.
        fake_sys.stdin.isatty.return_value = False
        app_ctx.return_value = mock.Mock()
        with self.assertRaises(typer.Exit):
            backfill_submit(
                "01TGT",
                occurred_since=None,
                occurred_until=None,
                completed_since="30d",
                completed_until=None,
                replace=False,
                dry_run=False,
                yes=False,
            )
        app_ctx.return_value.api.watch.submit_backfill.assert_not_called()

    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_additive_piped_with_yes_submits(self, app_ctx, _console, fake_sys) -> None:
        fake_sys.stdin.isatty.return_value = False
        ac = mock.Mock()
        ac.api.watch.submit_backfill.return_value = _job()
        app_ctx.return_value = ac
        backfill_submit(
            "01TGT",
            occurred_since=None,
            occurred_until=None,
            completed_since="30d",
            completed_until=None,
            replace=False,
            dry_run=False,
            yes=True,  # explicit consent for the piped submit
        )
        ac.api.watch.submit_backfill.assert_called_once()

    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_dry_run_not_honored_aborts(self, app_ctx, _console) -> None:
        # Server returned a preview WITHOUT dry_run (ignored ?dry_run): abort rather
        # than print "nothing queued" over what may have been a real backfill.
        ac = mock.Mock()
        ac.api.watch.preview_backfill.return_value = _preview(dry_run=False)
        app_ctx.return_value = ac
        with self.assertRaises(typer.Exit):
            backfill_submit(
                "01TGT",
                occurred_since="7d",
                occurred_until=None,
                completed_since=None,
                completed_until=None,
                replace=True,
                dry_run=True,
                yes=False,
            )

    @mock.patch("openmagpie.commands.backfill.typer.confirm", return_value=True)
    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_replace_confirm_accepted_submits(self, app_ctx, _console, fake_sys, _confirm) -> None:
        fake_sys.stdin.isatty.return_value = True  # interactive: preview + prompt shown
        ac = mock.Mock()
        ac.api.watch.preview_backfill.return_value = _preview(replace=True)  # shown before the confirm
        ac.api.watch.submit_backfill.return_value = _job(replace=True)
        app_ctx.return_value = ac
        backfill_submit(
            "01TGT",
            occurred_since=None,
            occurred_until=None,
            completed_since="30d",
            completed_until=None,
            replace=True,
            dry_run=False,
            yes=False,  # confirm() -> True proceeds
        )
        ac.api.watch.preview_backfill.assert_called_once()  # size shown before committing
        ac.api.watch.submit_backfill.assert_called_once()

    @mock.patch("openmagpie.commands.backfill.typer.confirm", return_value=False)
    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_replace_confirm_declined_aborts(self, app_ctx, _console, fake_sys, _confirm) -> None:
        fake_sys.stdin.isatty.return_value = True
        ac = mock.Mock()
        ac.api.watch.preview_backfill.return_value = _preview(replace=True)
        app_ctx.return_value = ac
        with self.assertRaises(typer.Exit):
            backfill_submit(
                "01TGT",
                occurred_since=None,
                occurred_until=None,
                completed_since="30d",
                completed_until=None,
                replace=True,
                dry_run=False,
                yes=False,  # confirm() -> False aborts
            )
        ac.api.watch.submit_backfill.assert_not_called()

    @mock.patch("openmagpie.commands.backfill.typer.confirm", return_value=True)
    @mock.patch("openmagpie.commands.backfill.sys")
    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_additive_interactive_previews_then_confirms(self, app_ctx, _console, fake_sys, _confirm) -> None:
        # Even additive submit confirms interactively (like `watch action add`),
        # showing the size first, so an operator never silently queues LLM-cost runs.
        fake_sys.stdin.isatty.return_value = True
        ac = mock.Mock()
        ac.api.watch.preview_backfill.return_value = _preview(replace=False)
        ac.api.watch.submit_backfill.return_value = _job()
        app_ctx.return_value = ac
        backfill_submit(
            "01TGT",
            occurred_since=None,
            occurred_until=None,
            completed_since="30d",
            completed_until=None,
            replace=False,
            dry_run=False,
            yes=False,
        )
        ac.api.watch.preview_backfill.assert_called_once()
        ac.api.watch.submit_backfill.assert_called_once()

    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_status_reads_the_job(self, app_ctx, _console) -> None:
        ac = mock.Mock()
        ac.api.watch.get_backfill.return_value = _job(state="done", matched=5, enqueued=4)
        app_ctx.return_value = ac
        backfill_status("01JOB", jsonl=False, output=None)
        ac.api.watch.get_backfill.assert_called_once_with("01JOB")

    @mock.patch("openmagpie.commands.backfill.console")
    @mock.patch("openmagpie.commands.backfill.app_ctx")
    def test_list_reads_the_collection(self, app_ctx, _console) -> None:
        ac = mock.Mock()
        ac.api.watch.list_backfills.return_value = BackfillListResponse(items=[_job(state="done")], next_cursor=None)
        app_ctx.return_value = ac
        backfill_list(
            after=None, limit=None, columns=None, transpose=False, print_columns=False, jsonl=False, output=None
        )
        # Paged via _emit_columns_paginated -> fetch(cursor, limit) -> list_backfills(after=, limit=).
        ac.api.watch.list_backfills.assert_called_once_with(after=None, limit=None)


if __name__ == "__main__":
    unittest.main()
