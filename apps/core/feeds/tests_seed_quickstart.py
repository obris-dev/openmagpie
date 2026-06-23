"""Tests for the `seed_quickstart` management command.

The seed provisions a local dev account and wires the quickstart's feed + watch
(from config/templates/quickstart/, with a backfill watermark) in one call. It
has no LLM dependency, so these tests drive it directly via `call_command` and
assert on the persisted rows.
"""

import io
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.services import AccountService, UserProfileService, UserService
from feeds.models import Feed
from feeds.services import FeedService, SourceService
from watches.models import Watch
from watches.services import WatchActionService, WatchService


class SeedQuickstartTests(TestCase):
    def setUp(self) -> None:
        # The seed dumps the config to the real repo config/quickstart/
        # (BASE_DIR-relative, via the bind mount). Stub it so these DB-focused
        # tests never touch the working tree; the dump's file-writing is covered
        # by ConfigDumpTests.
        patcher = mock.patch("feeds.management.commands.seed_quickstart.config_dump.dump_config")
        self.mock_dump = patcher.start()
        self.addCleanup(patcher.stop)

    def _account_id(self, email: str = "local@openmagpie.local") -> str:
        user = UserService.Global.get_by_email(email)
        profile = UserProfileService.Global.primary_for_user(user_id=str(user.id))
        assert profile is not None
        return str(profile.account_id)

    def test_seeds_account_user_feed_and_watch(self) -> None:
        call_command("seed_quickstart", days=3)

        self.assertTrue(UserService.Global.email_exists("local@openmagpie.local"))
        account_id = self._account_id()
        self.assertEqual(AccountService.Global.get(account_id).name, "Local workspace")

        feeds = FeedService(account_id=account_id).list(limit=200)
        self.assertEqual(len(feeds), 1)
        feed = feeds[0]
        self.assertEqual(feed.kind, "curated")

        sources = SourceService(account_id=account_id).list(feed)
        self.assertEqual(len(sources), 2)

        # Each source's first-tick watermark is ~ now - days (tolerance for the
        # wall clock between command run and assertion).
        expected = timezone.now() - timedelta(days=3)
        for src in sources:
            self.assertIsNotNone(src.last_event_at)
            self.assertLess(abs((src.last_event_at - expected).total_seconds()), 120)

        watches = WatchService(account_id=account_id).list(limit=200)
        self.assertEqual(len(watches), 1)

    def test_skip_data_creates_account_only(self) -> None:
        # --skip-data brings up the account (so the user can sign in and explore)
        # but seeds no feed/watch.
        out = io.StringIO()
        call_command("seed_quickstart", skip_data=True, stdout=out)

        self.assertTrue(UserService.Global.email_exists("local@openmagpie.local"))
        account_id = self._account_id()
        self.assertEqual(AccountService.Global.get(account_id).name, "Local workspace")
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)
        # Login creds are surfaced so the caller can echo them in its summary.
        self.assertIn("local@openmagpie.local", out.getvalue())

    def test_watch_wired_to_feed_with_template_values(self) -> None:
        call_command("seed_quickstart", days=3)
        account_id = self._account_id()

        feed = FeedService(account_id=account_id).list(limit=200)[0]
        watch_svc = WatchService(account_id=account_id)
        watch = watch_svc.list(limit=200)[0]
        self.assertTrue(watch.is_active)
        # The template's stable names.
        self.assertEqual(feed.name, "Quickstart feed")
        self.assertEqual(watch.name, "Quickstart listener")

        self.assertEqual(watch_svc.feed_ids(watch), [str(feed.id)])

        action_svc = WatchActionService(account_id=account_id)
        actions = action_svc.list_for_path(str(watch.initial_path_id))
        self.assertEqual([a.kind for a in actions], ["semantic_filter", "log"])
        # Template values: 0.8 threshold, the [quickstart] log prefix.
        self.assertEqual(actions[0].config["threshold"], 0.8)
        self.assertTrue(actions[0].config["instructions"])
        self.assertEqual(actions[1].config["prefix"], "[quickstart]")

    def test_subreddits_and_instructions_personalize_the_seed(self) -> None:
        # The interactive seed passes the user's answers through; they replace
        # the template's sources + filter instructions. r/ prefix stripped and
        # the duplicate dropped (see _seed_config_dump.clean_subreddits).
        call_command(
            "seed_quickstart",
            days=1,
            subreddits="r/LocalLLaMA, MachineLearning, LocalLLaMA",
            instructions="someone asking which open-source model to run locally",
        )
        account_id = self._account_id()
        feed = FeedService(account_id=account_id).list(limit=200)[0]
        sources = SourceService(account_id=account_id).list(feed)
        # Slugs are stored bare + lowercased by the spec validator (the r/ prefix
        # and the LocalLLaMA duplicate both collapse).
        self.assertEqual(sorted(s.spec["subreddit"] for s in sources), ["localllama", "machinelearning"])

        watch = WatchService(account_id=account_id).list(limit=200)[0]
        actions = WatchActionService(account_id=account_id).list_for_path(str(watch.initial_path_id))
        self.assertEqual(actions[0].config["instructions"], "someone asking which open-source model to run locally")
        # Names stay the template's stable ones (not derived from the input).
        self.assertEqual(feed.name, "Quickstart feed")
        self.assertEqual(watch.name, "Quickstart listener")
        # The dump is fed the same personalized dicts.
        self.assertTrue(self.mock_dump.called)

    def test_all_invalid_subreddits_warns_and_keeps_defaults(self) -> None:
        # Typed something, but it all cleaned to nothing (a pasted URL, a spaced
        # phrase): warn rather than silently fall back, and keep the template's.
        err = io.StringIO()
        call_command("seed_quickstart", days=1, subreddits="reddit.com/r/foo, a phrase", stderr=err)
        account_id = self._account_id()
        feed = FeedService(account_id=account_id).list(limit=200)[0]
        sources = SourceService(account_id=account_id).list(feed)
        self.assertEqual(sorted(s.spec["subreddit"] for s in sources), ["opensource", "selfhosted"])
        self.assertIn("looked like subreddit names", err.getvalue())

    def test_threshold_personalizes(self) -> None:
        # A typed threshold replaces the template's 0.8.
        call_command("seed_quickstart", days=1, threshold="0.5")
        account_id = self._account_id()
        watch = WatchService(account_id=account_id).list(limit=200)[0]
        actions = WatchActionService(account_id=account_id).list_for_path(str(watch.initial_path_id))
        self.assertEqual(actions[0].config["threshold"], 0.5)

    def test_empty_personalization_keeps_the_template(self) -> None:
        # The non-interactive path passes empty strings; the template is seeded
        # unchanged (this is what CI / curl|sh-with-no-tty / the recording get).
        call_command("seed_quickstart", days=1, subreddits="", instructions="", threshold="")
        account_id = self._account_id()
        feed = FeedService(account_id=account_id).list(limit=200)[0]
        sources = SourceService(account_id=account_id).list(feed)
        self.assertEqual(sorted(s.spec["subreddit"] for s in sources), ["opensource", "selfhosted"])

    def test_dump_failure_does_not_brick_the_seed(self) -> None:
        # The config dump is best-effort: the feed + watch are already committed,
        # so a write failure (read-only mount, full disk) must warn but still let
        # the summary (login creds, ids) print, and leave a usable seed behind.
        self.mock_dump.side_effect = OSError("read-only file system")
        out, err = io.StringIO(), io.StringIO()
        call_command("seed_quickstart", days=1, stdout=out, stderr=err)
        account_id = self._account_id()
        self.assertEqual(len(FeedService(account_id=account_id).list(limit=200)), 1)
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)
        self.assertIn("could not write config/", err.getvalue())
        self.assertIn("local@openmagpie.local", out.getvalue())  # the summary still prints

    def test_print_config_defaults_emits_template_values(self) -> None:
        out = io.StringIO()
        call_command("seed_quickstart", print_config_defaults=True, stdout=out)
        printed = out.getvalue()
        self.assertIn("subreddits=selfhosted, opensource", printed)
        self.assertIn("instructions=", printed)
        self.assertIn("threshold=0.8", printed)
        # Each key prints exactly once: the shell parser keeps the last `key=`
        # match, so a duplicate line would silently win.
        lines = printed.splitlines()
        for key in ("subreddits=", "instructions=", "threshold="):
            self.assertEqual(sum(line.startswith(key) for line in lines), 1, key)
        # Side-effect-free, like --print-activity.
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)

    def test_print_activity_outputs_gate_action_id(self) -> None:
        # --print-activity prints (only) the seeded semantic_filter action id so
        # `make local-seed` can echo a paste-ready `activity list --action` after
        # the tick. No seeding side effects: counts are unchanged.
        call_command("seed_quickstart", days=3)
        account_id = self._account_id()
        action_svc = WatchActionService(account_id=account_id)
        watch = WatchService(account_id=account_id).list(limit=200)[0]
        gate = next(a for a in action_svc.list_for_path(str(watch.initial_path_id)) if a.kind == "semantic_filter")

        out = io.StringIO()
        call_command("seed_quickstart", print_activity=True, stdout=out)
        self.assertEqual(out.getvalue().strip(), str(gate.id))
        # Read-only: still exactly one feed + one watch.
        self.assertEqual(len(FeedService(account_id=account_id).list(limit=200)), 1)
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)

    def test_print_activity_no_seed_prints_nothing(self) -> None:
        # Nothing seeded yet: print-activity is silent (the make recipe falls
        # back to a generic hint), and creates nothing.
        out = io.StringIO()
        call_command("seed_quickstart", print_activity=True, stdout=out)
        self.assertEqual(out.getvalue().strip(), "")
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)

    def test_watch_create_failure_cleans_up_the_feed(self) -> None:
        # The two creates are deliberately NOT one transaction.atomic() (that
        # would re-nest the chain lock inside a transaction and release it
        # before commit; see apps/core/AGENTS.md). Instead a watch-create
        # failure deletes the feed by hand, so no orphan is left behind.
        with (
            mock.patch.object(WatchService, "create", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            call_command("seed_quickstart", days=3)
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)

    def test_idempotent_second_call_no_duplicates(self) -> None:
        call_command("seed_quickstart", days=3)
        call_command("seed_quickstart", days=3)

        account_id = self._account_id()
        self.assertEqual(len(FeedService(account_id=account_id).list(limit=200)), 1)
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)

    def test_reset_rebuilds(self) -> None:
        call_command("seed_quickstart", days=3)
        account_id = self._account_id()
        first_feed_id = FeedService(account_id=account_id).list(limit=200)[0].id

        call_command("seed_quickstart", days=3, reset=True)
        feeds = FeedService(account_id=account_id).list(limit=200)
        self.assertEqual(len(feeds), 1)
        self.assertNotEqual(feeds[0].id, first_feed_id)  # rebuilt, a new row
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)

    def test_partial_seed_requires_reset(self) -> None:
        # A partial seed (feed present, watch gone via an out-of-band delete) is
        # NOT silently rebuilt: we don't delete anything without --reset.
        call_command("seed_quickstart", days=3)
        account_id = self._account_id()
        feed_svc = FeedService(account_id=account_id)
        watch_svc = WatchService(account_id=account_id)
        watch_svc.delete(watch_svc.list(limit=200)[0])

        # No --reset: left untouched, not rebuilt.
        call_command("seed_quickstart", days=3)
        self.assertEqual(len(feed_svc.list(limit=200)), 1)
        self.assertEqual(len(watch_svc.list(limit=200)), 0)

        # --reset: clears the partial and rebuilds both.
        call_command("seed_quickstart", days=3, reset=True)
        self.assertEqual(len(feed_svc.list(limit=200)), 1)
        self.assertEqual(len(watch_svc.list(limit=200)), 1)

    def test_malformed_action_raises_commanderror(self) -> None:
        # A typo'd kind in the (operator-editable) template surfaces as a clean
        # CommandError, not a raw traceback, and no feed is left behind.
        bad_feed = {"name": "x", "sources": [{"spec": {"kind": "reddit_subreddit", "subreddit": "a"}}]}
        bad_watch = {"name": "y", "actions": [{"kind": "nope", "config": {}}]}
        with (
            mock.patch(
                "feeds.management.commands.seed_quickstart.Command._load_template",
                return_value=(bad_feed, bad_watch),
            ),
            self.assertRaises(CommandError),
        ):
            call_command("seed_quickstart", days=3)
        self.assertEqual(Feed.objects.count(), 0)

    def test_unparsable_template_yaml_raises_commanderror(self) -> None:
        # A hand-edited config that no longer parses gives a clean error naming
        # the file, not a raw YAML traceback.
        from feeds.management.commands.seed_quickstart import Command

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("name: x\nsources: [unclosed\n")  # invalid: unclosed flow sequence
            bad = Path(f.name)
        try:
            with self.assertRaises(CommandError) as ctx:
                Command._load_yaml(bad)
            self.assertIn(bad.name, str(ctx.exception))
            self.assertIn("valid YAML", str(ctx.exception))
        finally:
            bad.unlink()

    def test_non_mapping_yaml_raises_commanderror(self) -> None:
        # Empty or a top-level list/scalar is not a mapping: clean error, not
        # a downstream TypeError when we index it.
        from feeds.management.commands.seed_quickstart import Command

        for content in ("", "- just\n- a\n- list\n"):
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
                f.write(content)
                path = Path(f.name)
            try:
                with self.assertRaises(CommandError) as ctx:
                    Command._load_yaml(path)
                self.assertIn("mapping", str(ctx.exception))
            finally:
                path.unlink()

    def test_negative_days_raises_commanderror(self) -> None:
        # A negative lookback is a future watermark (feeds/policy rejects it);
        # surface a clean CommandError before creating anything.
        with self.assertRaises(CommandError):
            call_command("seed_quickstart", days=-1)
        self.assertEqual(Feed.objects.count(), 0)
        self.assertFalse(UserService.Global.email_exists("local@openmagpie.local"))

    @override_settings(IS_CLOUD=True)
    def test_gated_to_local(self) -> None:
        with self.assertRaises(CommandError):
            call_command("seed_quickstart", days=3)
        # Nothing created.
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)
        self.assertFalse(UserService.Global.email_exists("local@openmagpie.local"))

    @mock.patch.dict(os.environ, {"SEED_EMAIL": "dev@example.test", "SEED_PASSWORD": "another-secret"})
    def test_env_overridable_login(self) -> None:
        call_command("seed_quickstart", days=3)
        self.assertTrue(UserService.Global.email_exists("dev@example.test"))
        self.assertFalse(UserService.Global.email_exists("local@openmagpie.local"))
