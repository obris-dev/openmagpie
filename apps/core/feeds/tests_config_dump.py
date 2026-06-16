"""Unit tests for the seed config-dump helpers (`_seed_config_dump`): parsing
the prompt's subreddit / threshold input, reading template defaults, overriding
the filter, and writing the editable `config/quickstart/` YAML. Pure (no DB)."""

import tempfile
from pathlib import Path

import yaml
from django.test import SimpleTestCase

from feeds.management.commands import _seed_config_dump as cd


class CleanSubredditsTests(SimpleTestCase):
    def test_strips_r_prefix_spaces_blanks_and_dups(self) -> None:
        self.assertEqual(
            cd.clean_subreddits("r/LocalLLaMA, MachineLearning , , LocalLLaMA, r/selfhosted"),
            ["LocalLLaMA", "MachineLearning", "selfhosted"],
        )

    def test_empty_yields_empty_list(self) -> None:
        self.assertEqual(cd.clean_subreddits(""), [])
        self.assertEqual(cd.clean_subreddits("  ,  "), [])

    def test_drops_invalid_names_keeping_the_valid_ones(self) -> None:
        # A pasted URL, a spaced phrase, a hyphen: dropped (would fail opaquely
        # at poll time), the valid neighbor kept. All-invalid yields [], which
        # the command treats as "keep the template".
        self.assertEqual(
            cd.clean_subreddits("reddit.com/r/foo, machine learning, valid_one, has-dash"),
            ["valid_one"],
        )
        self.assertEqual(cd.clean_subreddits("reddit.com/r/foo, machine learning"), [])


class TemplateExtractTests(SimpleTestCase):
    _FEED = {
        "sources": [
            {"spec": {"kind": "reddit_subreddit", "subreddit": "selfhosted"}},
            {"spec": {"kind": "reddit_subreddit", "subreddit": "opensource"}},
            {"spec": {"kind": "rss", "url": "https://x.test/feed"}},  # non-reddit: ignored
        ]
    }
    _WATCH = {
        "actions": [
            {"kind": "semantic_filter", "config": {"instructions": "find X", "threshold": 0.8}},
            {"kind": "log", "config": {"prefix": "[x]"}},
        ]
    }

    def test_template_subreddits_skips_non_reddit(self) -> None:
        self.assertEqual(cd.template_subreddits(self._FEED), ["selfhosted", "opensource"])

    def test_filter_instructions_reads_the_semantic_filter(self) -> None:
        self.assertEqual(cd.filter_instructions(self._WATCH), "find X")

    def test_filter_instructions_none_when_absent(self) -> None:
        self.assertIsNone(cd.filter_instructions({"actions": [{"kind": "log", "config": {}}]}))

    def test_set_filter_instructions_overrides_in_place(self) -> None:
        watch = {"actions": [{"kind": "semantic_filter", "config": {"instructions": "old"}}]}
        cd.set_filter_instructions(watch, "new")
        self.assertEqual(watch["actions"][0]["config"]["instructions"], "new")

    def test_filter_threshold_reads_and_sets(self) -> None:
        self.assertEqual(cd.filter_threshold(self._WATCH), 0.8)
        watch = {"actions": [{"kind": "semantic_filter", "config": {"threshold": 0.8}}]}
        cd.set_filter_threshold(watch, 0.5)
        self.assertEqual(watch["actions"][0]["config"]["threshold"], 0.5)
        # bool is an int subclass; `threshold: true` must read as absent, not 1.0.
        self.assertIsNone(
            cd.filter_threshold({"actions": [{"kind": "semantic_filter", "config": {"threshold": True}}]})
        )

    def test_parse_threshold_clamps_and_rejects(self) -> None:
        self.assertEqual(cd.parse_threshold("0.5"), 0.5)
        self.assertEqual(cd.parse_threshold("2"), 1.0)  # clamped
        self.assertEqual(cd.parse_threshold("-1"), 0.0)  # clamped
        self.assertIsNone(cd.parse_threshold(""))  # empty -> keep template's
        self.assertIsNone(cd.parse_threshold("loose"))  # non-numeric -> keep template's


class DumpConfigTests(SimpleTestCase):
    def test_writes_reappliable_yaml_with_real_feed_id(self) -> None:
        # No `data` key here on purpose: the dump must emit it anyway, since
        # FeedCreateSerializer requires it for the re-apply round-trip.
        feed = {
            "name": "F",
            "kind": "curated",
            "sources": [{"spec": {"kind": "reddit_subreddit", "subreddit": "selfhosted"}}],
        }
        watch = {
            "name": "W",
            "feed_ids": ["REPLACE_WITH_FEED_ID"],
            "actions": [{"kind": "semantic_filter", "config": {"instructions": "find X", "threshold": 0.8}}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "config"
            dest = cd.dump_config(root, feed, watch, "01FEEDID")
            self.assertEqual(dest, root / "quickstart")
            feed_out = yaml.safe_load((dest / "feed.yaml").read_text())
            watch_out = yaml.safe_load((dest / "watch.yaml").read_text())
            # The README + template are committed, so the dump writes neither.
            self.assertFalse((dest / "README.md").exists())
            self.assertFalse((root / "README.md").exists())
        # Faithful watch: the real feed id replaces the placeholder.
        self.assertEqual(watch_out["feed_ids"], ["01FEEDID"])
        # Feed sources are carried through, without a last_event_at watermark.
        self.assertEqual(feed_out["sources"], [{"spec": {"kind": "reddit_subreddit", "subreddit": "selfhosted"}}])
        self.assertNotIn("last_event_at", feed_out["sources"][0])
        # `data` is emitted even though the input omitted it (serializer requires it).
        self.assertEqual(feed_out["data"], {})
