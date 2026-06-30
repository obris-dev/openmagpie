"""Unit tests for `feed`/`watch` pause + resume (the is_active toggle).

Stdlib unittest. Run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest
from unittest import mock

from openmagpie.api.feed import FeedInput


class FeedInputActiveTests(unittest.TestCase):
    def test_is_active_defaults_true_and_round_trips(self) -> None:
        self.assertTrue(FeedInput(name="t").is_active)  # create stays active unless told otherwise
        self.assertFalse(FeedInput(name="t", is_active=False).is_active)  # create paused


class PauseResumeCommandTests(unittest.TestCase):
    """`pause`/`resume` send the one-bit toggle via set_active (PATCH), not a full edit."""

    @mock.patch("openmagpie.commands.feed._lifecycle.console")
    @mock.patch("openmagpie.commands.feed._lifecycle.app_ctx")
    def test_feed_pause_then_resume(self, app_ctx, _console) -> None:
        from openmagpie.commands.feed._lifecycle import pause, resume

        ac = mock.Mock()
        app_ctx.return_value = ac
        pause("FID")
        ac.api.feed.set_active.assert_called_once_with("FID", is_active=False)
        ac.api.feed.set_active.reset_mock()
        resume("FID")
        ac.api.feed.set_active.assert_called_once_with("FID", is_active=True)

    @mock.patch("openmagpie.commands.watch._lifecycle.console")
    @mock.patch("openmagpie.commands.watch._lifecycle.app_ctx")
    def test_watch_pause_then_resume(self, app_ctx, _console) -> None:
        from openmagpie.commands.watch._lifecycle import pause, resume

        ac = mock.Mock()
        app_ctx.return_value = ac
        pause("WID")
        ac.api.watch.set_active.assert_called_once_with("WID", is_active=False)
        ac.api.watch.set_active.reset_mock()
        resume("WID")
        ac.api.watch.set_active.assert_called_once_with("WID", is_active=True)


class ActiveFlipNoteTests(unittest.TestCase):
    """`edit -f` PUTs the whole envelope, and an omitted is_active defaults true, so a
    bare -f file would silently un-pause. _active_flip_note warns on any pause-state flip."""

    def test_no_note_when_state_unchanged(self) -> None:
        from openmagpie.commands._shared import _active_flip_note

        self.assertIsNone(_active_flip_note(current=True, submitted=True, noun="feed", resource_id="FID"))
        self.assertIsNone(_active_flip_note(current=False, submitted=False, noun="feed", resource_id="FID"))

    def test_warns_and_points_at_the_verb_on_flip(self) -> None:
        from openmagpie.commands._shared import _active_flip_note

        resumed = _active_flip_note(current=False, submitted=True, noun="feed", resource_id="FID")
        self.assertIn("magpie feed resume FID", resumed)
        paused = _active_flip_note(current=True, submitted=False, noun="watch", resource_id="WID")
        self.assertIn("magpie watch pause WID", paused)


if __name__ == "__main__":
    unittest.main()
