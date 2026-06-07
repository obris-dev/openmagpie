"""Tests for the public waitlist: signup endpoint, invite service, command.

The endpoint view uses ScopedRateThrottle, which reads the cache; the test DB
has no DatabaseCache table, so these override CACHES to locmem (which also lets
the throttle work in-memory). EmailService is mocked everywhere so no test
touches the render service or SMTP.
"""

from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from waitlist.constants import WaitlistState
from waitlist.models import WaitlistSignup
from waitlist.services import WaitlistService

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
_SEND = "waitlist.services.waitlist.EmailService.send_template"


@override_settings(CACHES=_LOCMEM)
class WaitlistSignupEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    @mock.patch(_SEND)
    def test_signup_creates_pending_and_welcomes(self, send: mock.Mock) -> None:
        resp = self.client.post("/v1/waitlist", {"email": "a@example.com", "source": "hero"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        row = WaitlistSignup.objects.get()
        self.assertEqual(row.email, "a@example.com")
        self.assertEqual(row.state, WaitlistState.PENDING.value)
        self.assertEqual(row.source, "hero")
        self.assertIsNone(row.invited_at)
        send.assert_called_once()

    @mock.patch(_SEND)
    def test_signup_is_idempotent(self, send: mock.Mock) -> None:
        self.client.post("/v1/waitlist", {"email": "a@example.com"}, format="json")
        resp = self.client.post("/v1/waitlist", {"email": "a@example.com"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        # Welcome email only fires on the first (new) signup.
        send.assert_called_once()

    @mock.patch(_SEND)
    def test_email_is_normalized(self, _send: mock.Mock) -> None:
        self.client.post("/v1/waitlist", {"email": "  MixedCase@Example.COM "}, format="json")
        self.assertTrue(WaitlistSignup.objects.filter(email="mixedcase@example.com").exists())

    @mock.patch(_SEND)
    def test_invalid_email_is_rejected(self, send: mock.Mock) -> None:
        resp = self.client.post("/v1/waitlist", {"email": "not-an-email"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(WaitlistSignup.objects.exists())
        send.assert_not_called()

    @mock.patch(_SEND, side_effect=ValueError("malformed render response"))
    def test_welcome_failure_does_not_break_signup(self, _send: mock.Mock) -> None:
        # Any welcome-email failure (not just OSError/EmailRenderError — a
        # malformed render response raises ValueError) is logged but must not
        # fail the signup.
        resp = self.client.post("/v1/waitlist", {"email": "a@example.com"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(WaitlistSignup.objects.filter(email="a@example.com").exists())


@override_settings(CACHES=_LOCMEM)
class WaitlistInviteTests(TestCase):
    @mock.patch(_SEND)
    def test_mark_invited_flips_state_and_stamps_time(self, send: mock.Mock) -> None:
        signup = WaitlistSignup.objects.create(email="a@example.com")
        WaitlistService.mark_invited(signup)
        signup.refresh_from_db()
        self.assertEqual(signup.state, WaitlistState.INVITED.value)
        self.assertIsNotNone(signup.invited_at)
        send.assert_called_once()

    @mock.patch(_SEND)
    def test_command_dry_run_changes_nothing(self, send: mock.Mock) -> None:
        WaitlistSignup.objects.create(email="a@example.com")
        call_command("send_waitlist_invites", dry_run=True)
        self.assertEqual(WaitlistSignup.objects.get().state, WaitlistState.PENDING.value)
        send.assert_not_called()

    @mock.patch(_SEND)
    def test_command_invites_pending_respecting_limit(self, send: mock.Mock) -> None:
        WaitlistSignup.objects.create(email="a@example.com")
        WaitlistSignup.objects.create(email="b@example.com")
        call_command("send_waitlist_invites", limit=1)
        self.assertEqual(WaitlistSignup.objects.filter(state=WaitlistState.INVITED.value).count(), 1)
        self.assertEqual(send.call_count, 1)
