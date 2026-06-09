import io
import signal
from collections.abc import Callable
from typing import cast
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.test import SimpleTestCase, override_settings

from common.commands import SingleFlightCommand, _sigterm_as_systemexit
from common.email import EmailRenderError, EmailService
from common.locks import job_lock_key, named_lock

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "lock-tests"}}


@override_settings(CACHES=_LOCMEM)
class NamedLockLeaseTests(SimpleTestCase):
    """The cache-backed try-lock + its renewable lease."""

    def setUp(self) -> None:
        cache.clear()

    def test_second_holder_is_blocked_then_released(self) -> None:
        with named_lock(name="k", timeout=60) as a:
            self.assertTrue(a)
            with named_lock(name="k", timeout=60) as b:
                self.assertFalse(b)  # held -> miss
        # Released on exit: a fresh acquire succeeds.
        with named_lock(name="k", timeout=60) as c:
            self.assertTrue(c)

    def test_renew_extends_while_held(self) -> None:
        with named_lock(name="k", timeout=60) as lease:
            self.assertTrue(lease.renew())  # still ours
            self.assertTrue(lease.acquired)

    def test_renew_false_when_taken_over_and_release_spares_new_owner(self) -> None:
        with named_lock(name="k", timeout=60) as lease:
            # Simulate the lease expiring under us + another holder acquiring.
            cache.set("k", "someone-else", timeout=60)
            self.assertFalse(lease.renew())  # we no longer own it
            self.assertFalse(lease)  # __bool__ flips to False
        # Our finally must NOT delete the new owner's key.
        self.assertEqual(cache.get("k"), "someone-else")

    def test_missed_lock_never_renews(self) -> None:
        with named_lock(name="k", timeout=60), named_lock(name="k", timeout=60) as miss:
            self.assertFalse(miss)
            self.assertFalse(miss.renew())


class ResolveJobNameTests(SimpleTestCase):
    """`SingleFlightCommand.resolve_job_name`, the single-flight lock key."""

    def test_defaults_to_app_qualified_command_name(self) -> None:
        """For a real management-command module the key is the
        app-qualified `<app>.<command>` (so same-named commands in two apps
        don't share a lock)."""

        class Command(SingleFlightCommand):
            pass

        Command.__module__ = "watches.management.commands.process_due_runs"
        self.assertEqual(Command().resolve_job_name(), "watches.process_due_runs")

    def test_class_variable_overrides_default(self) -> None:
        """An explicit `job_name` wins over the derived file name (and
        bypasses the module-path requirement, by design)."""

        class Command(SingleFlightCommand):
            job_name = "shared_pipeline"

        Command.__module__ = "watches.services.something"
        self.assertEqual(Command().resolve_job_name(), "shared_pipeline")

    def test_non_command_module_without_override_raises(self) -> None:
        """Deriving the default outside a management-command module would
        lock on a misleading key, so it fails loud instead."""

        class Command(SingleFlightCommand):
            pass

        Command.__module__ = "watches.services.something"
        with self.assertRaises(ImproperlyConfigured):
            Command().resolve_job_name()


@override_settings(CACHES=_LOCMEM)
class SigtermReleaseTests(SimpleTestCase):
    """SIGTERM frees the job lock (the finally runs), not just SIGKILL/TTL."""

    def setUp(self) -> None:
        cache.clear()

    def test_handler_raises_systemexit_then_restores_prior(self) -> None:
        original = signal.getsignal(signal.SIGTERM)
        with _sigterm_as_systemexit():
            installed = signal.getsignal(signal.SIGTERM)
            self.assertIsNot(installed, original)  # our handler is in place
            # cast past getsignal's Handler|int|None union to invoke it directly
            # (no real signal needed to prove it raises).
            handler = cast("Callable[[int, object], None]", installed)
            with self.assertRaises(SystemExit):
                handler(signal.SIGTERM, None)
        self.assertIs(signal.getsignal(signal.SIGTERM), original)  # restored on exit

    def test_lock_released_when_run_exits_via_systemexit(self) -> None:
        # SystemExit is what the SIGTERM handler raises; the lock must release
        # through job_lock's finally rather than orphaning until the TTL.
        class Command(SingleFlightCommand):
            job_name = "test.exiting"

        with (
            mock.patch.object(BaseCommand, "execute", side_effect=SystemExit(143)),
            self.assertRaises(SystemExit),
        ):
            Command().execute()
        self.assertIsNone(cache.get(job_lock_key("test.exiting")))


@override_settings(CACHES=_LOCMEM)
class ClearJobLocksTests(SimpleTestCase):
    """`clear_job_locks`: the manual override after a hard kill."""

    def setUp(self) -> None:
        cache.clear()

    def test_clears_a_named_job(self) -> None:
        cache.add(job_lock_key("feeds.poll_due_feeds"), "tok", timeout=300)
        call_command("clear_job_locks", "--job", "feeds.poll_due_feeds", stdout=io.StringIO())
        self.assertIsNone(cache.get(job_lock_key("feeds.poll_due_feeds")))

    def test_requires_job_or_all(self) -> None:
        with self.assertRaises(CommandError):
            call_command("clear_job_locks", stdout=io.StringIO())

    def test_unknown_job_name_warns_but_still_clears(self) -> None:
        # A typo'd --job would otherwise look like a successful no-op; warn on
        # stderr (survives a stdout redirect in scripting).
        err = io.StringIO()
        call_command("clear_job_locks", "--job", "feeds.poll_due_feedz", stdout=io.StringIO(), stderr=err)
        self.assertIn("not a known single-flight job", err.getvalue())

    def test_known_job_name_does_not_warn(self) -> None:
        err = io.StringIO()
        call_command("clear_job_locks", "--job", "feeds.poll_due_feeds", stdout=io.StringIO(), stderr=err)
        self.assertNotIn("not a known", err.getvalue())

    def test_all_discovers_and_clears_singleflight_jobs(self) -> None:
        # poll_due_feeds is a real SingleFlightCommand, so --all finds its lock
        # name via the registry and clears it (no hardcoded list).
        key = job_lock_key("feeds.poll_due_feeds")
        cache.add(key, "tok", timeout=300)
        call_command("clear_job_locks", "--all", stdout=io.StringIO())
        self.assertIsNone(cache.get(key))

    def test_dry_run_reports_without_deleting(self) -> None:
        # The up-jobs pre-flight: report held locks, delete nothing.
        key = job_lock_key("feeds.poll_due_feeds")
        cache.add(key, "tok", timeout=300)
        out = io.StringIO()
        call_command("clear_job_locks", "--all", "--dry-run", stdout=out)
        self.assertIn("HELD", out.getvalue())
        self.assertIn("feeds.poll_due_feeds", out.getvalue())
        self.assertEqual(cache.get(key), "tok")  # NOT cleared

    def test_dry_run_clean_when_nothing_held(self) -> None:
        out = io.StringIO()
        call_command("clear_job_locks", "--all", "--dry-run", stdout=out)
        self.assertIn("no job locks currently held", out.getvalue())

    def test_discover_skips_a_job_it_cannot_resolve(self) -> None:
        # A misconfigured SingleFlightCommand (resolve_job_name raises) must be
        # skipped, not abort the whole --all sweep (the incident-response path).
        from common.management.commands import clear_job_locks

        class _Bad(SingleFlightCommand):
            pass

        _Bad.__module__ = "badapp.services.notacommand"  # no job_name + bad module -> raises
        with (
            mock.patch.object(clear_job_locks, "get_commands", return_value={"bad": "badapp"}),
            mock.patch.object(clear_job_locks, "load_command_class", return_value=_Bad()),
        ):
            self.assertEqual(clear_job_locks.Command._discover_jobs(), set())  # skipped, did not raise

    @override_settings(IS_CLOUD=True)
    def test_all_in_cloud_requires_yes(self) -> None:
        # --all on a shared (cloud) cache is high blast-radius: gate it.
        with self.assertRaises(CommandError):
            call_command("clear_job_locks", "--all", stdout=io.StringIO())

    @override_settings(IS_CLOUD=True)
    def test_all_in_cloud_proceeds_with_yes(self) -> None:
        key = job_lock_key("feeds.poll_due_feeds")
        cache.add(key, "tok", timeout=300)
        call_command("clear_job_locks", "--all", "--yes", stdout=io.StringIO())
        self.assertIsNone(cache.get(key))

    @override_settings(IS_CLOUD=True)
    def test_cloud_does_not_gate_dry_run_or_targeted_job(self) -> None:
        # Read-only (--dry-run) and precise (--job) stay unrestricted in cloud.
        key = job_lock_key("feeds.poll_due_feeds")
        cache.add(key, "tok", timeout=300)
        call_command("clear_job_locks", "--all", "--dry-run", stdout=io.StringIO())  # no raise
        self.assertEqual(cache.get(key), "tok")  # dry-run deletes nothing
        call_command("clear_job_locks", "--job", "feeds.poll_due_feeds", stdout=io.StringIO())  # no raise
        self.assertIsNone(cache.get(key))


@override_settings(EMAIL_RENDER_URL="http://render.test", EMAIL_TIMEOUT=10)
class EmailRenderTemplateTests(SimpleTestCase):
    """render_template converts EVERY render failure to EmailRenderError, so the
    documented contract holds and callers can catch one type."""

    @staticmethod
    def _response(*, json_value=None, json_exc=None):
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()  # 2xx, no-op
        resp.json = mock.Mock(side_effect=json_exc) if json_exc else mock.Mock(return_value=json_value)
        return resp

    def test_unconfigured_url_raises(self) -> None:
        with override_settings(EMAIL_RENDER_URL=""), self.assertRaises(EmailRenderError):
            EmailService.render_template(template="x", props={})

    @mock.patch("common.email.httpx.post")
    def test_non_json_body_raises(self, post: mock.Mock) -> None:
        post.return_value = self._response(json_exc=ValueError("not json"))
        with self.assertRaises(EmailRenderError):
            EmailService.render_template(template="x", props={})

    @mock.patch("common.email.httpx.post")
    def test_missing_keys_raise(self, post: mock.Mock) -> None:
        # success truthy but no html / plainText -> KeyError, still wrapped.
        post.return_value = self._response(json_value={"success": True})
        with self.assertRaises(EmailRenderError):
            EmailService.render_template(template="x", props={})

    @mock.patch("common.email.httpx.post")
    def test_success_false_raises(self, post: mock.Mock) -> None:
        post.return_value = self._response(json_value={"success": False, "error": "boom"})
        with self.assertRaises(EmailRenderError):
            EmailService.render_template(template="x", props={})

    @mock.patch("common.email.httpx.post")
    def test_ok_returns_html_and_plain_text(self, post: mock.Mock) -> None:
        post.return_value = self._response(json_value={"success": True, "html": "<p>hi</p>", "plainText": "hi"})
        out = EmailService.render_template(template="x", props={})
        self.assertEqual(out, {"html": "<p>hi</p>", "plainText": "hi"})
