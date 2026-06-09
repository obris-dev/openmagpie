"""Seed an example feed + watch into a local dev account.

Local only (gated to DJANGO_ENV=local, never cloud) and needs no auth: a
fresh clone reaches a real match in one command. The seed only creates
data (account, user, feed, watch); ticking the pipeline is a separate
step. Each source's first-tick watermark is set to `now - days` so the
opening tick scores a backlog instead of only brand-new posts.

Matches surface in the terminal (the log action's `[starter]` lines) and
in the CLI activity log, NOT the web UI (the web UI does not render
matches yet).
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from accounts.services import AccountService, UserProfileService, UserService
from feeds.services import FeedService
from openmagpie_schema.feed import SourceInput
from openmagpie_schema.watch import WatchActionInput
from watches.registry import validate_config
from watches.services import WatchService

DEFAULT_STARTER = "selfhosted-opensource"
DEFAULT_DAYS = 3
DEFAULT_EMAIL = "local@openmagpie.local"
DEFAULT_PASSWORD = "openmagpie-local"


class Command(BaseCommand):
    help = "Seed an example feed + watch into the local dev account (local only, no auth)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--starter", type=str, default=DEFAULT_STARTER, help="Starter dir under examples/starters/")
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS, help="Lookback window for the first-tick backfill"
        )
        parser.add_argument("--reset", action="store_true", help="Delete the existing seeded feed + watch and rebuild")

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_CLOUD:
            raise CommandError("seed_quickstart is local only (DJANGO_ENV=local)")

        starter = options["starter"]
        days = options["days"]
        reset = options["reset"]

        user_id, account_id, email, password = self._get_or_create_account()
        feed_yaml, watch_yaml = self._load_starter(starter)

        feed_svc = FeedService(account_id=account_id)
        watch_svc = WatchService(account_id=account_id)

        existing_feed = self._find_by_name(feed_svc.list(limit=200), feed_yaml["name"])
        if existing_feed is not None:
            if not reset:
                self.stdout.write("Already seeded (pass --reset to rebuild).")
                return
            existing_watch = self._find_by_name(watch_svc.list(limit=200), watch_yaml["name"])
            if existing_watch is not None:
                watch_svc.delete(existing_watch)
            feed_svc.delete(existing_feed)

        watermark = timezone.now() - timedelta(days=days)
        sources = [SourceInput(**s).model_copy(update={"last_event_at": watermark}) for s in feed_yaml["sources"]]
        feed = feed_svc.create(
            user_id=user_id,
            name=feed_yaml["name"],
            kind=feed_yaml.get("kind", "curated"),
            poll_interval_seconds=feed_yaml.get("poll_interval_seconds", 300),
            data=feed_yaml.get("data", {}),
            sources=sources,
        )

        actions = self._build_actions(watch_yaml["actions"])
        watch = watch_svc.create(
            user_id=user_id,
            name=watch_yaml["name"],
            is_active=True,
            feed_ids=[str(feed.id)],
            actions=actions,
        )

        self._report(feed, watch, sources, days, email, password)

    def _get_or_create_account(self) -> tuple[str, str, str, str]:
        email = os.environ.get("SEED_EMAIL", DEFAULT_EMAIL).strip().lower()
        password = os.environ.get("SEED_PASSWORD", DEFAULT_PASSWORD)
        if UserService.Global.email_exists(email):
            user = UserService.Global.get_by_email(email)
        else:
            user = UserService.Global.create(email=email, password=password)
        user_id = str(user.id)

        profile = UserProfileService.Global.primary_for_user(user_id=user_id)
        if profile is not None:
            account_id = str(profile.account_id)
        else:
            account = AccountService.Global.create(name="Local workspace")
            account_id = str(account.id)
            UserProfileService.Global.bind_owner(user_id=user_id, account_id=account_id)
        return user_id, account_id, email, password

    def _load_starter(self, starter: str) -> tuple[dict[str, Any], dict[str, Any]]:
        # BASE_DIR is apps/core; the repo root is two levels up (apps/core -> apps -> root).
        repo_root = Path(settings.BASE_DIR).parent.parent
        starters = repo_root / "examples" / "starters" / starter
        if not starters.is_dir():
            raise CommandError(f"no starter dir at {starters}")
        feed_path = starters / "feed.yaml"
        watch_path = starters / "watch.yaml"
        for path in (feed_path, watch_path):
            if not path.is_file():
                raise CommandError(f"missing starter file {path}")
        feed_yaml = yaml.safe_load(feed_path.read_text())
        watch_yaml = yaml.safe_load(watch_path.read_text())
        return feed_yaml, watch_yaml

    @staticmethod
    def _find_by_name(rows: list[Any], name: str) -> Any | None:
        for row in rows:
            if row.name == name:
                return row
        return None

    @staticmethod
    def _build_actions(raw_actions: list[dict[str, Any]]) -> list[WatchActionInput]:
        # Mirror the watch serializer: validate_config gives the shape +
        # policy checked typed config, and the persisted blob is its dump.
        # A bad config raises pydantic ValidationError / PolicyError.
        actions = []
        for raw in raw_actions:
            kind = raw["kind"]
            typed = validate_config(kind, raw["config"])
            actions.append(WatchActionInput(id="", kind=kind, config=typed.model_dump(mode="json")))
        return actions

    def _report(self, feed: Any, watch: Any, sources: list[Any], days: int, email: str, password: str) -> None:
        self.stdout.write(f"Seeded as {email} (Local workspace). Account login: {email} / {password}")
        self.stdout.write(f"Feed {feed.id} ({len(sources)} sources, looking back {days}d). Watch {watch.id}.")
        self.stdout.write(
            "Matches print to the terminal when the chain runs (the `[starter]` log lines), e.g. via `make local-tick`."
        )
        self.stdout.write("Inspect runs with: `magpie watch action activity <action_id>` (after `magpie auth login`).")
