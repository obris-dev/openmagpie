"""Telemetry tests, focused on the privacy-critical contract.

The load-bearing properties: telemetry is opt-OUT, so the UNSET default and
ANONYMOUS both emit and only an explicit OFF is silent; DO_NOT_TRACK is honored,
the capture path never raises, the instance_id is anonymous + stable, IDENTIFIED
is refused on self-hosted, the surface tag can't be spoofed, and the setter is
owner-only.
"""

import io
import os
from unittest import mock

from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.constants import PROFILE_STATUS_REVOKED
from accounts.models import User, UserProfile
from accounts.services import AccountService, UserProfileService
from telemetry import client, events
from telemetry.middleware import SurfaceMiddleware
from telemetry.models import TelemetryMode, TelemetrySettings
from telemetry.service import TelemetryService
from telemetry.views import TelemetryView


class CaptureGatingTests(TestCase):
    @mock.patch("telemetry.client.get_client")
    def test_emits_by_default_when_unset(self, get_client):
        fake = get_client.return_value
        client.capture("e")  # opt-OUT: the default UNSET mode emits
        fake.capture.assert_called_once()
        args, kwargs = fake.capture.call_args
        self.assertEqual(args[0], "e")
        # the instance_id is minted at row creation, so the default row has one
        self.assertEqual(kwargs["distinct_id"], TelemetrySettings.current().instance_id)

    @mock.patch("telemetry.client.get_client")
    def test_no_op_when_off(self, get_client):
        fake = get_client.return_value
        TelemetryService.Global.set_mode(TelemetryMode.OFF.value)
        fake.capture.reset_mock()
        client.capture("e")
        fake.capture.assert_not_called()

    @mock.patch("telemetry.client.get_client")
    def test_emits_when_identified(self, get_client):
        # The send/no-send gate is deployment-agnostic (emit unless OFF), so IDENTIFIED
        # -- the hosted, account-keyed mode -- emits too. set_mode refuses it on
        # self-hosted; the gate never special-cases it (how it's keyed is capture's job).
        fake = get_client.return_value
        row = TelemetrySettings.current()
        TelemetrySettings.objects.filter(pk=row.pk).update(mode=TelemetryMode.IDENTIFIED.value)
        fake.capture.reset_mock()
        client.capture("e")
        fake.capture.assert_called_once()

    @mock.patch("telemetry.client.get_client")
    def test_emits_when_anonymous(self, get_client):
        fake = get_client.return_value
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)
        fake.capture.reset_mock()
        client.capture("e", {"a": 1})
        fake.capture.assert_called_once()
        args, kwargs = fake.capture.call_args
        self.assertEqual(args[0], "e")
        self.assertEqual(kwargs["distinct_id"], TelemetrySettings.current().instance_id)
        self.assertEqual(kwargs["properties"]["a"], 1)
        self.assertIn("version", kwargs["properties"])

    @mock.patch("telemetry.client.get_client")
    def test_do_not_track_suppresses(self, get_client):
        fake = get_client.return_value
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)
        fake.capture.reset_mock()
        with mock.patch.dict(os.environ, {"DO_NOT_TRACK": "1"}):
            client.capture("e")
        fake.capture.assert_not_called()

    @mock.patch("telemetry.client.get_client", return_value=None)
    def test_no_client_is_a_silent_no_op(self, _):
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)
        client.capture("e")  # must not raise

    @mock.patch("telemetry.client.get_client")
    def test_capture_never_raises(self, get_client):
        get_client.return_value.capture.side_effect = RuntimeError("boom")
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)
        client.capture("e")  # the raising client must not propagate


class InstanceIdAndModeTests(TestCase):
    @mock.patch("telemetry.client.get_client")
    def test_instance_id_generated_once_and_stable(self, _):
        first = TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value).instance_id
        self.assertTrue(first)
        TelemetryService.Global.set_mode(TelemetryMode.OFF.value)
        self.assertEqual(TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value).instance_id, first)

    def test_set_mode_refuses_identified_and_unset(self):
        for bad in (TelemetryMode.IDENTIFIED.value, TelemetryMode.UNSET.value, "bogus"):
            with self.assertRaises(ValueError):
                TelemetryService.Global.set_mode(bad)

    @mock.patch("telemetry.client.get_client")
    def test_set_enabled_resolves_intent_to_mode(self, _):
        # enable/disable is the consent intent; the service resolves it to a concrete
        # mode (self-hosted: enable -> anonymous, disable -> off).
        self.assertEqual(TelemetryService.Global.set_enabled(enabled=True).mode, TelemetryMode.ANONYMOUS.value)
        self.assertEqual(TelemetryService.Global.set_enabled(enabled=False).mode, TelemetryMode.OFF.value)

    @mock.patch("telemetry.client.get_client")
    def test_reenable_after_off_emits_telemetry_enabled_once(self, get_client):
        fake = get_client.return_value
        TelemetryService.Global.set_mode(TelemetryMode.OFF.value)  # opt out first
        fake.capture.reset_mock()
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)  # off -> anonymous: a real re-enable
        self.assertIn("telemetry_enabled", [c.args[0] for c in fake.capture.call_args_list])
        fake.capture.reset_mock()
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)  # already anonymous -> no re-emit
        fake.capture.assert_not_called()

    @mock.patch("telemetry.client.get_client")
    def test_affirming_the_default_does_not_emit_telemetry_enabled(self, get_client):
        # unset already emits (opt-out); unset -> anonymous changes nothing observable,
        # so it must not fire a "turned on" event.
        fake = get_client.return_value
        TelemetryService.Global.set_mode(TelemetryMode.ANONYMOUS.value)  # from the unset default
        self.assertNotIn("telemetry_enabled", [c.args[0] for c in fake.capture.call_args_list])


class GuardTests(SimpleTestCase):
    def test_guard_swallows_exceptions(self):
        with events.guard():
            raise RuntimeError("boom")  # must not propagate

    @mock.patch("telemetry.client.capture")
    def test_feed_created_props_are_sorted_deduped(self, capture):
        events.feed_created(source_count=3, connector_kinds=["rss", "rss", "hn_feed"], surface="cli")
        event, props = capture.call_args.args
        self.assertEqual(event, "feed_created")
        self.assertEqual(props["connector_kinds"], ["hn_feed", "rss"])
        self.assertEqual(props["surface"], "cli")


class SurfaceMiddlewareTests(SimpleTestCase):
    def _surface(self, **meta) -> str:
        req = RequestFactory().get("/", **meta)
        captured: dict[str, str] = {}

        def get_response(r):
            captured["s"] = r.surface
            return HttpResponse()

        SurfaceMiddleware(get_response)(req)
        return captured["s"]

    def test_allowlisted_header_wins(self):
        self.assertEqual(self._surface(HTTP_X_MAGPIE_SURFACE="cli"), "cli")
        self.assertEqual(self._surface(HTTP_X_MAGPIE_SURFACE="web"), "web")

    def test_bogus_header_cannot_be_injected(self):
        # An unknown value is ignored; falls back to UA, then api.
        self.assertEqual(self._surface(HTTP_X_MAGPIE_SURFACE="evil", HTTP_USER_AGENT="magpie-cli/1.0"), "cli")
        # "system" is server-only (not in the inbound allowlist); a client
        # claiming it via the header is ignored, falling back to api.
        self.assertEqual(self._surface(HTTP_X_MAGPIE_SURFACE="system"), "api")

    def test_user_agent_fallback_then_api(self):
        self.assertEqual(self._surface(HTTP_USER_AGENT="magpie-cli/1.0 (Darwin)"), "cli")
        self.assertEqual(self._surface(HTTP_USER_AGENT="Mozilla/5.0"), "api")


class TelemetryEndpointTests(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()

    @staticmethod
    def _owner(email: str) -> User:
        """A user who owns their account -- the self-hosted operator who may set it."""
        user = User.objects.create_user(email=email, password="p")
        account = AccountService.Global.create(name="t")
        UserProfileService.Global.bind_owner(user_id=str(user.id), account_id=str(account.id))
        return user

    @mock.patch("telemetry.client.get_client")
    def test_get_can_set_false_for_non_owner(self, _):
        user = User.objects.create_user(email="member@x.io", password="p")  # no owner profile
        req = self.factory.get("/v1/telemetry")
        force_authenticate(req, user=user)
        resp = TelemetryView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["mode"], TelemetryMode.UNSET.value)
        self.assertFalse(resp.data["can_set"])

    @mock.patch("telemetry.client.get_client")
    def test_get_can_set_true_for_owner(self, _):
        req = self.factory.get("/v1/telemetry")
        force_authenticate(req, user=self._owner("owner-get@x.io"))
        resp = TelemetryView.as_view()(req)
        self.assertTrue(resp.data["can_set"])
        # Server computes emission (the CLI can't): opt-out default (unset) is emitting.
        self.assertTrue(resp.data["emitting"])

    @mock.patch("telemetry.client.get_client")
    def test_setter_forbidden_for_non_owner(self, _):
        user = User.objects.create_user(email="member2@x.io", password="p")  # no owner profile
        req = self.factory.post("/v1/telemetry", {"enabled": True}, format="json")
        force_authenticate(req, user=user)
        resp = TelemetryView.as_view()(req)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(TelemetrySettings.current().mode, TelemetryMode.UNSET.value)  # unchanged

    @mock.patch("telemetry.client.get_client")
    def test_setter_allows_owner(self, _):
        req = self.factory.post("/v1/telemetry", {"enabled": True}, format="json")
        force_authenticate(req, user=self._owner("owner-set@x.io"))
        resp = TelemetryView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["mode"], TelemetryMode.ANONYMOUS.value)
        # POST returns emitting like GET (not the null "old server" sentinel).
        self.assertTrue(resp.data["emitting"])

    @mock.patch("telemetry.client.get_client")
    def test_setter_rejects_missing_enabled(self, _):
        # The endpoint takes a consent intent {enabled: bool}; a missing/garbage body
        # is a 400, not a 500. (identified is server-internal, unreachable from here.)
        req = self.factory.post("/v1/telemetry", {}, format="json")
        force_authenticate(req, user=self._owner("owner-id@x.io"))
        resp = TelemetryView.as_view()(req)
        self.assertEqual(resp.status_code, 400)

    @mock.patch("telemetry.client.get_client")
    def test_setter_forbidden_for_revoked_owner(self, _):
        # role=owner but status=revoked must NOT pass the active-owner gate -- the
        # gate checks status, not just role (defense-in-depth for a future revoke flow).
        user = self._owner("revoked-owner@x.io")
        UserProfile.objects.filter(user_id=str(user.id)).update(status=PROFILE_STATUS_REVOKED)
        req = self.factory.post("/v1/telemetry", {"enabled": True}, format="json")
        force_authenticate(req, user=user)
        resp = TelemetryView.as_view()(req)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(TelemetrySettings.current().mode, TelemetryMode.UNSET.value)  # unchanged


class HeartbeatThrottleTests(TestCase):
    """The heartbeat rides the pipeline tickers, so it must self-throttle: a no-op
    when opted out (OFF), and at most one emit per window however often it's called."""

    @mock.patch("telemetry.client.get_client")
    def test_no_op_when_off(self, get_client):
        TelemetryService.Global.set_mode(TelemetryMode.OFF.value)  # opt-OUT: only OFF is silent
        call_command("emit_telemetry_heartbeat")
        get_client.return_value.capture.assert_not_called()
        self.assertIsNone(TelemetrySettings.current().last_heartbeat_at)

    @mock.patch(
        "telemetry.management.commands.emit_telemetry_heartbeat.Command._engine_reachable",
        return_value=False,
    )
    @mock.patch("telemetry.client.get_client")
    def test_emits_once_then_throttles_within_window(self, get_client, _engine):
        fake = get_client.return_value
        # opt-OUT: the default UNSET mode emits, no explicit opt-in needed

        call_command("emit_telemetry_heartbeat")
        self.assertIn("instance_heartbeat", [c.args[0] for c in fake.capture.call_args_list])
        stamped = TelemetrySettings.current().last_heartbeat_at
        self.assertIsNotNone(stamped)

        fake.capture.reset_mock()
        call_command("emit_telemetry_heartbeat")  # again, within _MIN_INTERVAL
        self.assertNotIn("instance_heartbeat", [c.args[0] for c in fake.capture.call_args_list])
        self.assertEqual(TelemetrySettings.current().last_heartbeat_at, stamped)


class StatusCommandTests(TestCase):
    """`telemetry status` is the transparency surface TELEMETRY.md points operators
    at, so its `emitting:` line must track the real (opt-out) gate, not a stale copy
    of it (this regressed once: it kept gating on is_anonymous after the flip)."""

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_default_unset_reports_emitting_yes(self):
        out = io.StringIO()
        call_command("telemetry", "status", stdout=out)  # opt-out: the default emits
        self.assertRegex(out.getvalue(), r"emitting:\s+yes")

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_off_reports_emitting_no(self):
        TelemetryService.Global.set_mode(TelemetryMode.OFF.value)
        out = io.StringIO()
        call_command("telemetry", "status", stdout=out)
        self.assertRegex(out.getvalue(), r"emitting:\s+no")


class TestRunnerGuardTests(SimpleTestCase):
    def test_posthog_sdk_is_stubbed_for_the_whole_suite(self):
        # The custom TEST_RUNNER (conf.test_runner) patches posthog.Posthog so opt-out
        # telemetry (the UNSET default emits) can't ship real events from a throwaway
        # test DB. If this fails, the suite is one un-mocked capture() away from emitting
        # to the shared project -- the regression flipping to opt-out introduced.
        import posthog

        self.assertIsInstance(posthog.Posthog, mock.MagicMock)


class GetClientTests(SimpleTestCase):
    """get_client builds the PostHog client once and caches it (the lock guards
    this under concurrent first-requests; here we pin the single-thread contract)."""

    def setUp(self) -> None:
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset() -> None:
        client._client_built = False
        client._client_instance = None

    @override_settings(POSTHOG_API_KEY="phc_test")
    def test_builds_once_and_caches(self):
        with mock.patch("posthog.Posthog") as posthog:
            first = client.get_client()
            second = client.get_client()
        self.assertIs(first, second)
        posthog.assert_called_once()
