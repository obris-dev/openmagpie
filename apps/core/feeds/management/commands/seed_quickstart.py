"""Seed the quickstart's feed + watch into a local dev account.

A local-development tool: it refuses to run when DJANGO_ENV=cloud (so it
is safe anywhere that is not cloud) and needs no auth, so a fresh clone
reaches a real match in one command. It builds from the template in
config/templates/quickstart/, personalized with the subreddits / instructions /
threshold the user chose, and dumps the result to config/quickstart/. The seed
only creates data (account, user, feed, watch); ticking the pipeline is a
separate step. Each source's first-tick watermark is set to `now - days` so the
opening tick scores a backlog instead of only brand-new posts.

Matches surface in the terminal (the log action's `[quickstart]` lines) and in
the CLI activity log, NOT the web UI (the web UI does not render matches yet).
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
from openmagpie_schema.feed import CuratedFeedConfig, SourceInput
from openmagpie_schema.watch_enums import WatchActionKind
from watches.services import WatchService

from . import _seed_config_dump as config_dump

# The quickstart's template lives at config/templates/quickstart/ (committed);
# the seed reads it, applies the user's personalization, and writes the result
# to config/quickstart/. The template carries the stable names (Quickstart feed /
# Quickstart listener) and the `[quickstart]` log prefix, so the seed's lookups
# (idempotency / --reset / --print-activity, all by name) stay findable on a
# re-run without overriding. Both paths hang off settings.REPO_ROOT / "config".
# The backfill is scored one LLM call per post on the first tick, so the
# window sets how long a new user waits for their first match. Wider
# lookback on demand: DAYS=7 seed.sh.
DEFAULT_DAYS = 1
DEFAULT_EMAIL = "local@openmagpie.local"
DEFAULT_PASSWORD = "openmagpie-local"


class Command(BaseCommand):
    help = "Seed an example feed + watch into the local dev account (local only, no auth)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days", type=int, default=DEFAULT_DAYS, help="Lookback window for the first-tick backfill"
        )
        parser.add_argument("--reset", action="store_true", help="Delete the existing seeded feed + watch and rebuild")
        parser.add_argument(
            "--skip-data",
            action="store_true",
            help="Create the local account but no feed/watch (start from an empty workspace).",
        )
        parser.add_argument(
            "--print-activity",
            action="store_true",
            help="Print the seeded watch's semantic_filter action id (for `activity list --action`) and exit; no seeding.",
        )
        parser.add_argument(
            "--subreddits",
            type=str,
            default="",
            help="Comma-separated subreddits to listen to, replacing the template's. Empty keeps the template's.",
        )
        parser.add_argument(
            "--instructions",
            type=str,
            default="",
            help="Plain-language semantic_filter instructions, replacing the template's. Empty keeps the template's.",
        )
        parser.add_argument(
            "--threshold",
            type=str,
            default="",
            help="Match threshold 0-1 for the semantic_filter, replacing the template's. Empty keeps the template's.",
        )
        parser.add_argument(
            "--print-config-defaults",
            action="store_true",
            help="Print the template's subreddits / instructions / threshold (for the seed prompt) and exit; no seeding.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if settings.IS_CLOUD:
            raise CommandError("seed_quickstart refuses to run when DJANGO_ENV=cloud")

        # Read-only id lookup (no create) so `make local-seed` can echo a
        # paste-ready `activity list --action <id>` after the tick. Nothing
        # printed if nothing is seeded yet; the caller falls back to a hint.
        if options["print_activity"]:
            action_id = self._lookup_gate_id()
            if action_id is not None:
                self.stdout.write(action_id)
            return

        # Side-effect-free, like --print-activity: emit the template's subreddits
        # / instructions / threshold (as `key=value` lines) for the seed prompt's
        # defaults so the shell needn't parse YAML.
        if options["print_config_defaults"]:
            feed_yaml, watch_yaml = self._load_template()
            self.stdout.write(f"subreddits={', '.join(config_dump.template_subreddits(feed_yaml))}")
            # Collapse whitespace so a block-scalar `instructions: |` can't break
            # the one-line `key=value` contract the shell parser relies on.
            instructions = " ".join((config_dump.filter_instructions(watch_yaml) or "").split())
            self.stdout.write(f"instructions={instructions}")
            threshold = config_dump.filter_threshold(watch_yaml)
            self.stdout.write(f"threshold={threshold if threshold is not None else ''}")
            return

        days = options["days"]
        reset = options["reset"]
        if days < 0:
            # A negative lookback is a future watermark, which feeds/policy.py
            # rejects (it would silently disable the source). Surface it as a
            # clean CommandError, like the command's other operator errors.
            raise CommandError(f"--days must be >= 0 (got {days})")

        user_id, account_id, account_name, email, password = self._get_or_create_account()

        if options["skip_data"]:
            # Account only: a workspace to explore, no seeded feed/watch.
            self.stdout.write(f"Account ready ({account_name}). Account login: {email} / {password}")
            self.stdout.write(
                "No example data seeded (--skip-data). Create one with `magpie feed create` / `magpie watch create`."
            )
            return

        feed_yaml, watch_yaml = self._load_template()

        # Personalization (from the interactive seed prompt): the user's
        # subreddits / instructions / threshold replace the template's in the
        # loaded dicts, so the seed AND the config dump build from one source.
        # Empty keeps the template's, so the non-interactive path / CI seed it
        # unchanged. Names + the [quickstart] prefix come from the template, so
        # there is nothing to override for the lookups below to stay findable.
        subreddits = config_dump.clean_subreddits(options["subreddits"])
        if subreddits:
            feed_yaml["sources"] = config_dump.subreddit_sources(subreddits)
        elif options["subreddits"].strip():
            # Typed something, but none of it parsed as a subreddit name (a
            # pasted URL, a spaced phrase): don't silently fall back to the
            # template's subreddits without a word.
            self.stderr.write("None of those looked like subreddit names; keeping the default subreddits.")
        instructions = options["instructions"].strip()
        if instructions:
            config_dump.set_filter_instructions(watch_yaml, instructions)
        threshold = config_dump.parse_threshold(options["threshold"])
        if threshold is not None:
            config_dump.set_filter_threshold(watch_yaml, threshold)

        feed_svc = FeedService(account_id=account_id)
        watch_svc = WatchService(account_id=account_id)

        # Never delete existing data without --reset: if any part is already
        # present (full seed, or a partial from an out-of-band delete) stop and
        # ask rather than silently clearing it.
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
        actions = config_dump.parse_actions(watch_yaml["actions"])
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

        # Dump the personalized config to config/quickstart/ for the user to read
        # and re-apply; built from the same dicts, lands in the clone via the bind
        # mount (.:/app). The README + template are committed, so the dump only
        # writes the two YAMLs. Best-effort: the feed + watch are already
        # committed, so a write failure (read-only mount, full disk, a stray
        # config/quickstart file) must NOT abort before _report below, which
        # carries the login + ids. OSError covers the lot.
        config_root = settings.REPO_ROOT / "config"
        try:
            config_dump.dump_config(config_root, feed_yaml, watch_yaml, str(feed.id))
        except OSError as exc:
            self.stderr.write(
                f"Seeded OK, but could not write config/ ({exc}). The feed + watch exist "
                "(`magpie feed list` / `magpie watch list`)."
            )

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

    def _load_template(self) -> tuple[dict[str, Any], dict[str, Any]]:
        # The template is committed at config/templates/quickstart/, so a missing
        # file is a broken checkout, surfaced as a clean CommandError, not a raw one.
        template_dir = settings.REPO_ROOT / "config" / "templates" / "quickstart"
        feed_path = template_dir / "feed.yaml"
        watch_path = template_dir / "watch.yaml"
        for path in (feed_path, watch_path):
            if not path.is_file():
                raise CommandError(f"quickstart template missing {path} (broken checkout?)")
        return self._load_yaml(feed_path), self._load_yaml(watch_path)

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        # The template + user configs are operator-editable, so a syntax slip
        # (bad indent, a stray tab, an unclosed bracket) or a non-mapping top
        # level surfaces as a clean CommandError naming the file, not a raw YAML
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

    def _lookup_gate_id(self) -> str | None:
        # Found by the template's (stable) watch name, the same name the seed
        # created it under, so this stays correct across re-runs.
        account_id = self._find_account_id()
        if account_id is None:
            return None
        _, watch_yaml = self._load_template()
        watch_svc = WatchService(account_id=account_id)
        watch = watch_svc.find_by_name(watch_yaml["name"])
        if watch is None:
            return None
        return self._gate_action_id(watch_svc.initial_actions(watch))

    @staticmethod
    def _gate_action_id(actions: list[Any]) -> str | None:
        # The chain's semantic_filter (the gate); its activity (the matched /
        # gated runs) is what a first user wants to inspect. None if absent.
        return next((str(a.id) for a in actions if str(a.kind) == WatchActionKind.SEMANTIC_FILTER), None)

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
        self.stdout.write("Matches print to the terminal when the pipeline runs (the watch's log lines).")
        # The config-location pointer is intentionally NOT printed here: under
        # the quickstart it scrolls off behind the tick output, so run.sh's
        # final summary carries it where the user actually reads.
        if gate_action_id is not None:
            self.stdout.write(
                f"See the filter: `magpie watch action get {gate_action_id}` ; what it matched: "
                f"`magpie activity list --action {gate_action_id}` (after `magpie auth login`)."
            )
        else:
            self.stdout.write(
                "Inspect runs with: `magpie activity list --action <action_id>` (after `magpie auth login`)."
            )
