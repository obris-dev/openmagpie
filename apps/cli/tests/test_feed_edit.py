"""Unit tests for `feed edit`'s sources-ignored warning.

Stdlib `unittest` (no pytest dependency in the CLI yet). Run with:
  uv run --package openmagpie python -m unittest discover -s apps/cli/tests
"""

from __future__ import annotations

import unittest

from openmagpie.api.feed import FeedInput
from openmagpie.commands.feed._crud import _FILE_PLACEHOLDER, _sources_ignored_note
from openmagpie_schema.feed import SourceInput

_PY = {"kind": "reddit_subreddit", "subreddit": "python"}
_GO = {"kind": "reddit_subreddit", "subreddit": "golang"}


def _src(spec: dict, **kw: object) -> SourceInput:
    """A current feed source — stand-in for the server's SourceWire (same
    .spec / .meta / .field_map surface the comparison reads)."""
    return SourceInput.model_validate({"spec": spec, **kw})


class SourcesIgnoredNoteTests(unittest.TestCase):
    """`feed edit` discards a `sources:` block server-side. The note fires only
    when the file would change what `feed source set` applies — a source
    added/removed, OR a persisted source's meta/field_map — and points there."""

    def test_no_sources_no_note(self) -> None:
        self.assertIsNone(_sources_ignored_note(FeedInput(name="t"), [_src(_PY)], "feed.yaml", "FID"))

    def test_empty_sources_is_a_documented_blind_spot(self) -> None:
        # A deliberate `sources: []` (full removal) is indistinguishable from an
        # omitted `sources:` after Pydantic defaults both to [], so it returns None
        # by design - warning on empty would false-fire on every metadata-only edit.
        self.assertIsNone(_sources_ignored_note(FeedInput(name="t", sources=[]), [_src(_PY)], "feed.yaml", "FID"))

    def test_in_sync_no_note(self) -> None:
        body = FeedInput(name="t", sources=[{"spec": _PY}])
        self.assertIsNone(_sources_ignored_note(body, [_src(_PY)], "feed.yaml", "FID"))

    def test_diverging_spec_warns(self) -> None:
        body = FeedInput(name="t", sources=[{"spec": _PY}])
        note = _sources_ignored_note(body, [_src(_GO)], "config/founders/feed.yaml", "01FEED")
        assert note is not None  # narrows for the asserts below
        self.assertIn("feed source set", note)
        self.assertIn("--feed 01FEED", note)
        self.assertIn("-f config/founders/feed.yaml", note)  # copy-pasteable

    def test_meta_only_change_warns(self) -> None:
        # Same spec, different meta: set_sources refreshes meta on the matched
        # row, so feed edit silently dropping it must warn (spec-only would miss).
        body = FeedInput(name="t", sources=[{"spec": _PY, "meta": {"tag": "new"}}])
        self.assertIsNotNone(_sources_ignored_note(body, [_src(_PY)], "feed.yaml", "FID"))

    def test_field_map_only_change_warns(self) -> None:
        body = FeedInput(name="t", sources=[{"spec": _PY, "field_map": {"body": "selftext"}}])
        self.assertIsNotNone(_sources_ignored_note(body, [_src(_PY)], "feed.yaml", "FID"))

    def test_rss_same_name_different_url_warns(self) -> None:
        # display() collides on the shared name ("Blog"); the spec comparison
        # catches the URL change so it isn't silently dropped.
        body = FeedInput(name="t", sources=[{"spec": {"kind": "rss", "url": "https://a.test/feed", "name": "Blog"}}])
        current = [_src({"kind": "rss", "url": "https://b.test/feed", "name": "Blog"})]
        self.assertIsNotNone(_sources_ignored_note(body, current, "feed.yaml", "FID"))

    def test_stdin_and_editor_get_a_placeholder(self) -> None:
        body = FeedInput(name="t", sources=[{"spec": _PY}])
        for source in ("-", None):  # stdin / $EDITOR have no file path to echo
            note = _sources_ignored_note(body, [], source, "01FEED")  # empty current -> diverges
            assert note is not None
            self.assertIn(f"-f {_FILE_PLACEHOLDER}", note)


class ParseSetPayloadErrorTests(unittest.TestCase):
    """`feed source set` renders a malformed BUILT-IN source spec as clean per-field
    errors, not the extensible-union's internal noise (shared clean_union_errors)."""

    def test_malformed_builtin_spec_renders_clean(self) -> None:
        from unittest import mock

        import typer

        from openmagpie.commands.feed._sources import _parse_set_payload

        text = '{"sources": [{"spec": {"kind": "rss", "url": "not-a-url"}}]}'
        # The per-field printing lives in the shared _abort_union_validation_error, so
        # patch the console it uses (not the caller module's).
        with (
            mock.patch("openmagpie.commands._shared.errors.console") as console_mock,
            self.assertRaises(typer.Exit),
        ):
            _parse_set_payload(text, "src.json")
        msgs = " ".join(str(c.args[0]) for c in console_mock.error.call_args_list)
        self.assertNotIn("tagged-union", msgs)  # union machinery stripped
        self.assertNotIn("built-in kind", msgs)  # plugin fallback's contract line dropped
        self.assertIn("url", msgs)  # the per-field path survives

    def test_padded_fallback_kind_renders_clean(self) -> None:
        from unittest import mock

        import typer

        from openmagpie.commands.feed._sources import _parse_set_payload

        text = '{"sources": [{"spec": {"kind": " rss ", "url": "https://x.test/f"}}]}'
        with (
            mock.patch("openmagpie.commands._shared.errors.console") as console_mock,
            self.assertRaises(typer.Exit),
        ):
            _parse_set_payload(text, "src.json")
        msgs = " ".join(str(c.args[0]) for c in console_mock.error.call_args_list)
        self.assertNotIn("does not match any of the expected tags", msgs)  # union_tag_invalid dropped
        self.assertNotIn("tagged-union", msgs)
        self.assertIn("padded", msgs)  # the real "kind must not be padded" error survives


if __name__ == "__main__":
    unittest.main()
