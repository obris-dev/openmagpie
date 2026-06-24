"""Unit tests for the watch-action editing helpers (no live server).

`_action_recreate_note` (the `watch edit` clobber warning) and
`_run_action_mutation` (the dry-run -> confirm -> apply flow shared by
`watch action add`/`edit`). Stdlib `unittest`; run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

import typer

from openmagpie.commands.watch._actions import _run_action_mutation
from openmagpie.commands.watch._crud import _action_recreate_note
from openmagpie_schema.watch import WatchActionInput, WatchActionMutationResponse, WatchActionWire


def _wire(action_id: str) -> WatchActionWire:
    return WatchActionWire(id=action_id, kind="log", rank=0)


def _inp(action_id: str = "") -> WatchActionInput:
    return WatchActionInput(id=action_id, kind="log")


class ActionRecreateNoteTests(unittest.TestCase):
    """`watch edit` matches the chain by id; an existing action whose id isn't
    resubmitted is dropped + recreated. The note fires only then."""

    def test_all_ids_resubmitted_no_note(self) -> None:
        note = _action_recreate_note([_wire("a"), _wire("b")], [_inp("a"), _inp("b")])
        self.assertIsNone(note)

    def test_no_current_actions_no_note(self) -> None:
        self.assertIsNone(_action_recreate_note([], [_inp("")]))

    def test_id_less_submission_lists_dropped_actions(self) -> None:
        note = _action_recreate_note([_wire("01AAA"), _wire("01BBB")], [_inp(""), _inp("")])  # neither carries an id
        assert note is not None  # narrows for the asserts below
        self.assertIn("2 existing action", note)  # the count + that they're dropped
        self.assertIn("won't complete", note)  # the consequence, in CLI terms
        self.assertIn("01AAA", note)  # dropped actions listed by id (copy-paste lookup)...
        self.assertIn("01BBB", note)
        self.assertIn("log", note)  # ...alongside their kind
        self.assertIn("magpie watch action edit", note)  # the in-place fix

    def test_partial_drop_lists_only_the_dropped(self) -> None:
        note = _action_recreate_note([_wire("01AAA"), _wire("01BBB")], [_inp("01AAA")])  # 01BBB's id absent -> dropped
        assert note is not None
        self.assertIn("1 existing action", note)  # only one dropped
        self.assertIn("01BBB", note)  # only the dropped one is listed
        self.assertNotIn("01AAA", note)  # the kept one is not


class RunActionMutationTests(unittest.TestCase):
    """The shared add/edit flow: dry-run preview, then `--yes`/confirm gates the
    real apply. `mutate(dry_run)` is called True (preview) then False (apply)."""

    def test_dry_run_stops_before_apply(self) -> None:
        mutate = mock.Mock(return_value=WatchActionMutationResponse(id=None, kind="log", rank=0, dry_run=True))
        result = _run_action_mutation(mutate, is_edit=False, dry_run=True, yes=False)  # add preview -> no id
        self.assertIsNone(result)  # nothing applied
        mutate.assert_called_once_with(True)  # only the preview ran

    def test_yes_applies_without_confirm(self) -> None:
        mutate = mock.Mock(
            side_effect=[
                WatchActionMutationResponse(id="01ABC", kind="log", rank=0, dry_run=True),  # edit preview keeps id
                WatchActionMutationResponse(id="01ABC", kind="log", rank=0, dry_run=False),
            ]
        )
        result = _run_action_mutation(mutate, is_edit=True, dry_run=False, yes=True)
        assert result is not None
        self.assertEqual(result.id, "01ABC")
        self.assertEqual(mutate.call_count, 2)  # preview + apply, no typer.confirm

    def test_aborts_when_server_ignores_dry_run(self) -> None:
        # Preview came back NOT flagged dry_run -> the server persisted; abort
        # before the apply call (defense-in-depth, mirrors _run_mutation).
        mutate = mock.Mock(return_value=WatchActionMutationResponse(id="01ABC", kind="log", rank=0, dry_run=False))
        with self.assertRaises(typer.Exit):
            _run_action_mutation(mutate, is_edit=True, dry_run=False, yes=True)
        mutate.assert_called_once_with(True)  # never reached the apply call

    def test_aborts_when_apply_does_not_persist(self) -> None:
        # Apply came back still dry_run / no id -> nothing persisted; abort.
        mutate = mock.Mock(
            side_effect=[
                WatchActionMutationResponse(id=None, kind="log", rank=0, dry_run=True),  # valid add preview
                WatchActionMutationResponse(id=None, kind="log", rank=0, dry_run=True),  # apply didn't persist
            ]
        )
        with self.assertRaises(typer.Exit):
            _run_action_mutation(mutate, is_edit=False, dry_run=False, yes=True)
        self.assertEqual(mutate.call_count, 2)  # reached apply, then aborted


if __name__ == "__main__":
    unittest.main()
