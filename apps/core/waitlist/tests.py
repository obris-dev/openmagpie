"""Tests for the public waitlist signup endpoint.

Signup ENQUEUES an OutboundEmail (the mailer drain sends it), so these assert
the queued row rather than mocking a send. The endpoint view uses
ScopedRateThrottle, so the class overrides CACHES to locmem (the test DB has no
DatabaseCache table).
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from mailer.constants import EmailState
from mailer.models import OutboundEmail
from waitlist.constants import WaitlistState
from waitlist.models import WaitlistSignup

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=_LOCMEM)
class WaitlistSignupEndpointTests(TestCase):
    def setUp(self) -> None:
        self.client = APIClient()

    def test_signup_creates_pending_and_enqueues_welcome(self) -> None:
        resp = self.client.post("/v1/waitlist", {"email": "a@example.com", "source": "hero"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        signup = WaitlistSignup.objects.get()
        self.assertEqual(signup.email, "a@example.com")
        self.assertEqual(signup.state, WaitlistState.PENDING.value)
        self.assertEqual(signup.source, "hero")
        self.assertIsNone(signup.invited_at)
        # A welcome email is queued (PENDING), keyed to the signup id.
        welcome = OutboundEmail.objects.get(template="waitlistWelcome")
        self.assertEqual(welcome.to_email, "a@example.com")
        self.assertEqual(welcome.state, EmailState.PENDING.value)
        self.assertEqual(welcome.idempotency_key, f"waitlist-welcome:{signup.id}")

    def test_signup_is_idempotent(self) -> None:
        self.client.post("/v1/waitlist", {"email": "a@example.com"}, format="json")
        resp = self.client.post("/v1/waitlist", {"email": "a@example.com"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        # Welcome enqueued once (idempotent), not duplicated on the second post.
        self.assertEqual(OutboundEmail.objects.filter(template="waitlistWelcome").count(), 1)

    def test_email_is_normalized(self) -> None:
        self.client.post("/v1/waitlist", {"email": "  MixedCase@Example.COM "}, format="json")
        self.assertTrue(WaitlistSignup.objects.filter(email="mixedcase@example.com").exists())

    def test_invalid_email_is_rejected(self) -> None:
        resp = self.client.post("/v1/waitlist", {"email": "not-an-email"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(WaitlistSignup.objects.exists())
        self.assertFalse(OutboundEmail.objects.exists())
