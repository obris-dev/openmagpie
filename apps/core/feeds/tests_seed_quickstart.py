"""Tests for the `seed_quickstart` management command.

The seed provisions a local dev account and wires an example feed + watch
(with a backfill watermark) in one call. It has no LLM dependency, so these
tests drive it directly via `call_command` and assert on the persisted rows.
"""

import os
from datetime import timedelta
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

STARTER = "selfhosted-opensource"


class SeedQuickstartTests(TestCase):
    def _account_id(self, email: str = "local@openmagpie.local") -> str:
        user = UserService.Global.get_by_email(email)
        profile = UserProfileService.Global.primary_for_user(user_id=str(user.id))
        assert profile is not None
        return str(profile.account_id)

    def test_seeds_account_user_feed_and_watch(self) -> None:
        call_command("seed_quickstart", starter=STARTER, days=3)

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

    def test_watch_wired_to_feed_with_validated_chain(self) -> None:
        call_command("seed_quickstart", starter=STARTER, days=3)
        account_id = self._account_id()

        feed = FeedService(account_id=account_id).list(limit=200)[0]
        watch_svc = WatchService(account_id=account_id)
        watch = watch_svc.list(limit=200)[0]
        self.assertTrue(watch.is_active)

        self.assertEqual(watch_svc.feed_ids(watch), [str(feed.id)])

        action_svc = WatchActionService(account_id=account_id)
        actions = action_svc.list_for_path(str(watch.initial_path_id))
        self.assertEqual([a.kind for a in actions], ["semantic_filter", "log"])
        # Configs are the registry-validated, normalized blobs.
        self.assertEqual(actions[0].config["threshold"], 0.6)
        self.assertTrue(actions[0].config["instructions"])
        self.assertEqual(actions[1].config["prefix"], "[starter]")

    def test_idempotent_second_call_no_duplicates(self) -> None:
        call_command("seed_quickstart", starter=STARTER, days=3)
        call_command("seed_quickstart", starter=STARTER, days=3)

        account_id = self._account_id()
        self.assertEqual(len(FeedService(account_id=account_id).list(limit=200)), 1)
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)

    def test_reset_rebuilds(self) -> None:
        call_command("seed_quickstart", starter=STARTER, days=3)
        account_id = self._account_id()
        first_feed_id = FeedService(account_id=account_id).list(limit=200)[0].id

        call_command("seed_quickstart", starter=STARTER, days=3, reset=True)
        feeds = FeedService(account_id=account_id).list(limit=200)
        self.assertEqual(len(feeds), 1)
        self.assertNotEqual(feeds[0].id, first_feed_id)  # rebuilt, a new row
        self.assertEqual(len(WatchService(account_id=account_id).list(limit=200)), 1)

    @override_settings(IS_CLOUD=True)
    def test_gated_to_local(self) -> None:
        with self.assertRaises(CommandError):
            call_command("seed_quickstart", starter=STARTER, days=3)
        # Nothing created.
        self.assertEqual(Feed.objects.count(), 0)
        self.assertEqual(Watch.objects.count(), 0)
        self.assertFalse(UserService.Global.email_exists("local@openmagpie.local"))

    @mock.patch.dict(os.environ, {"SEED_EMAIL": "dev@example.test", "SEED_PASSWORD": "another-secret"})
    def test_env_overridable_login(self) -> None:
        call_command("seed_quickstart", starter=STARTER, days=3)
        self.assertTrue(UserService.Global.email_exists("dev@example.test"))
        self.assertFalse(UserService.Global.email_exists("local@openmagpie.local"))
