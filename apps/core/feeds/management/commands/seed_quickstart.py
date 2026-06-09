"""Seed an example feed + watch into a local dev account.

A local-development tool: it refuses to run when DJANGO_ENV=cloud (so it
is safe anywhere that is not cloud) and needs no auth, so a fresh clone
reaches a real match in one command. The seed only creates
data (account, user, feed, watch); ticking the pipeline is a separate
step. Each source's first-tick watermark is set to `now - days` so the
opening tick scores a backlog instead of only brand-new posts.

Matches surface in the terminal (the log action's starter-prefixed lines,
e.g. `[oss starter]`) and in the CLI activity log, NOT the web UI (the web
UI does not render matches yet).
"""

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone
from pydantic import ValidationError

from accounts.services import AccountService, UserProfileService, UserService
from feeds.services import FeedService
from openmagpie_schema.feed import CuratedFeedConfig, SourceInput
from openmagpie_schema.watch import WatchActionInput
from openmagpie_schema.watch_enums import WatchActionKind
from watches.policy import PolicyError
from watches.registry import validate_config
from watches.services import WatchService

DEFAULT_STARTER = "selfhosted-opensource"
DEFAULT_DAYS = 3
DEFAULT_EMAIL = "local@openmagpie.local"
DEFAULT_PASSWORD = "openmagpie-local"


class Command(BaseCommand):
    help = "Seed an example feed + watch into the local dev account (local only, no auth)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--starter",
            type=str,
            default=DEFAULT_STARTER,
            help="Which starter to seed (a dir under examples/starters/; see examples/README.md). An unknown name lists the available ones.",
        )
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS, help="Lookback window for the first-tick backfill"
        )
        parser.add_argument("--reset", action="store_true", help="Delete the existing seeded feed + watch and rebuild")
        parser.add_argument(
            "--print-activity",
            action="store_true",
            help="Print the seeded watch's semantic_filter action id (for `watch action activity`) and exit; no seeding.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_CLOUD:
            raise CommandError("seed_quickstart refuses to run when DJANGO_ENV=cloud")

        starter = options["starter"]
        # Read-only id lookup (no create) so `make local-seed` can echo a
        # paste-ready `watch action activity <id>` after the tick. Nothing
        # printed if nothing is seeded yet; the caller falls back to a hint.
        if options["print_activity"]:
            action_id = self._lookup_gate_id(starter)
            if action_id is not None:
                self.stdout.write(action_id)
            return

        days = options["days"]
        reset = options["reset"]
        if days < 0:
            # A negative lookback is a future watermark, which feeds/policy.py
            # rejects (it would silently disable the source). Surface it as a
            # clean CommandError, like the command's other operator errors.
            raise CommandError(f"--days must be >= 0 (got {days})")

        user_id, account_id, account_name, email, password = self._get_or_create_account()
        feed_yaml, watch_yaml = self._load_starter(starter)

        feed_svc = FeedService(account_id=account_id)
        watch_svc = WatchService(account_id=account_id)

        # Never delete existing data without an explicit --reset. If ANY part
        # is already present (a full seed, or a partial left by an out-of-band
        # delete) we stop and ask, rather than silently clearing it. The create
        # below is atomic, so a failed run can't leave a partial on its own.
        existing_feed = feed_svc.find_by_name(feed_yaml["name"])
        existing_watch = watch_svc.find_by_name(watch_yaml["name"])
        if existing_feed is not None or existing_watch is not None:
            if not reset:
                state = "Already seeded" if (existing_feed and existing_watch) else "A partial seed exists"
                self.stdout.write(f"{state}. Pass --reset to delete the existing seeded feed/watch and rebuild.")
                return
            if existing_watch is not None:
                watch_svc.delete(existing_watch)
            if existing_feed is not None:
                feed_svc.delete(existing_feed)

        watermark = timezone.now() - timedelta(days=days)
        sources = [SourceInput(**s).model_copy(update={"last_event_at": watermark}) for s in feed_yaml["sources"]]
        actions = self._parse_actions(watch_yaml["actions"])
        # NB: do NOT wrap these two creates in one transaction.atomic().
        # WatchService.create runs replace_chain (which takes path_chain_lock)
        # OUTSIDE its own transaction on purpose; an outer atomic would re-nest
        # that lock inside a transaction and release it before commit, which
        # the chain-lock HARD RULE forbids (apps/core/AGENTS.md). To still avoid
        # an orphaned feed, delete it by hand if the watch fails (the --reset /
        # partial-seed path recovers it too, but this keeps a clean run clean).
        feed = feed_svc.create(
            user_id=user_id,
            name=feed_yaml["name"],
            kind=feed_yaml.get("kind", CuratedFeedConfig.FEED_KIND),
            poll_interval_seconds=feed_yaml.get("poll_interval_seconds", 300),
            data=feed_yaml.get("data", {}),
            sources=sources,
        )
        try:
            watch = watch_svc.create(
                user_id=user_id,
                name=watch_yaml["name"],
                is_active=True,
                feed_ids=[str(feed.id)],
                actions=actions,
            )
        except Exception:
            feed_svc.delete(feed)
            raise

        gate_action_id = self._gate_action_id(watch_svc.initial_actions(watch))
        self._report(feed, watch, sources, days, account_name, email, password, gate_action_id)

    @staticmethod
    def _seed_email() -> str:
        return os.environ.get("SEED_EMAIL", DEFAULT_EMAIL).strip().lower()

    @staticmethod
    def _existing_user(email: str) -> Any:
        # The seed user if it already exists, else None. Read-only (no create),
        # so both the get-or-create and the side-effect-free lookup share it.
        return UserService.Global.get_by_email(email) if UserService.Global.email_exists(email) else None

    def _get_or_create_account(self) -> tuple[str, str, str, str, str]:
        email = self._seed_email()
        password = os.environ.get("SEED_PASSWORD", DEFAULT_PASSWORD)
        user = self._existing_user(email) or UserService.Global.create(email=email, password=password)
        user_id = str(user.id)

        profile = UserProfileService.Global.primary_for_user(user_id=user_id)
        if profile is not None:
            account_id = str(profile.account_id)
        else:
            account = AccountService.Global.create(name="Local workspace")
            account_id = str(account.id)
            UserProfileService.Global.bind_owner(user_id=user_id, account_id=account_id)
        # Read the real name: a pre-existing account may not be "Local workspace".
        account_name = AccountService.Global.get(account_id).name
        return user_id, account_id, account_name, email, password

    def _load_starter(self, starter: str) -> tuple[dict[str, Any], dict[str, Any]]:
        # BASE_DIR is apps/core; the repo root is two levels up (apps/core -> apps -> root).
        starters_dir = Path(settings.BASE_DIR).parent.parent / "examples" / "starters"
        starter_dir = starters_dir / starter
        if not starter_dir.is_dir():
            available = sorted(p.name for p in starters_dir.iterdir() if p.is_dir()) if starters_dir.is_dir() else []
            options = ", ".join(available) or "(none found)"
            raise CommandError(f"unknown starter {starter!r}. Available: {options}. See examples/README.md.")
        feed_path = starter_dir / "feed.yaml"
        watch_path = starter_dir / "watch.yaml"
        for path in (feed_path, watch_path):
            if not path.is_file():
                raise CommandError(f"starter {starter!r} is missing {path.name}")
        return self._load_yaml(feed_path), self._load_yaml(watch_path)

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        # The starter files are operator-editable, so a syntax slip (bad
        # indent, a stray tab, an unclosed bracket) or a non-mapping top level
        # surfaces as a clean CommandError naming the file, not a raw YAML
        # traceback (or a downstream TypeError when we later index it).
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise CommandError(f"{path.name} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            got = "empty" if data is None else type(data).__name__
            raise CommandError(f"{path.name} must be a YAML mapping (got {got}).")
        return data

    def _find_account_id(self) -> str | None:
        # Resolve the seed account without creating one (the --print-activity
        # path must not have side effects). None if the seed user/profile is absent.
        user = self._existing_user(self._seed_email())
        if user is None:
            return None
        profile = UserProfileService.Global.primary_for_user(user_id=str(user.id))
        return str(profile.account_id) if profile is not None else None

    def _lookup_gate_id(self, starter: str) -> str | None:
        account_id = self._find_account_id()
        if account_id is None:
            return None
        _, watch_yaml = self._load_starter(starter)
        watch_svc = WatchService(account_id=account_id)
        watch = watch_svc.find_by_name(watch_yaml["name"])
        if watch is None:
            return None
        return self._gate_action_id(watch_svc.initial_actions(watch))

    @staticmethod
    def _gate_action_id(actions: list[Any]) -> str | None:
        # The chain's semantic_filter (the gate); its activity is the
        # matched-vs-filtered breakdown a first user wants. None if absent.
        return next((str(a.id) for a in actions if str(a.kind) == WatchActionKind.SEMANTIC_FILTER), None)

    @staticmethod
    def _parse_actions(raw_actions: list[dict[str, Any]]) -> list[WatchActionInput]:
        # Turn the raw starter action dicts into validated, IN-MEMORY
        # WatchActionInput objects. No DB writes here; WatchService.create
        # persists them. Mirrors the watch serializer: validate_config gives the
        # shape + policy checked typed config, and the stored blob is its dump.
        # A bad starter action surfaces as a clean CommandError.
        actions = []
        for i, raw in enumerate(raw_actions):
            try:
                kind = raw["kind"]
                config = raw["config"]
            except KeyError as exc:
                raise CommandError(f"starter action {i} is missing key {exc}") from exc
            try:
                typed = validate_config(kind, config)
            except KeyError as exc:
                raise CommandError(f"starter action {i} has unknown kind {exc}") from exc
            except (ValidationError, PolicyError) as exc:
                raise CommandError(f"starter action {i} ({kind!r}) is invalid: {exc}") from exc
            actions.append(WatchActionInput(id="", kind=kind, config=typed.model_dump(mode="json")))
        return actions

    def _report(
        self,
        feed: Any,
        watch: Any,
        sources: list[Any],
        days: int,
        account_name: str,
        email: str,
        password: str,
        gate_action_id: str | None,
    ) -> None:
        self.stdout.write(f"Seeded as {email} ({account_name}). Account login: {email} / {password}")
        self.stdout.write(f"Feed {feed.id} ({len(sources)} sources, looking back {days}d). Watch {watch.id}.")
        self.stdout.write("Matches print to the terminal when the pipeline runs (the starter's log lines).")
        if gate_action_id is not None:
            self.stdout.write(
                f"Inspect matched vs filtered: `magpie watch action activity {gate_action_id}` (after `magpie auth login`)."
            )
        else:
            self.stdout.write(
                "Inspect runs with: `magpie watch action activity <action_id>` (after `magpie auth login`)."
            )
