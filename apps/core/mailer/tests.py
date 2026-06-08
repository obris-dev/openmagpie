"""Tests for the outbound-email queue: enqueue, claim/complete/fail/reap, drain.

EmailService is mocked so no test touches the render service or SMTP. The drain
command uses a cache-backed single-flight lock, so its class overrides CACHES to
locmem (the test DB has no DatabaseCache table).
"""

from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from mailer.constants import EmailState
from mailer.models import OutboundEmail
from mailer.services import MailerService

_SEND = "mailer.management.commands.send_outbound_emails.EmailService.send_template"
_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _enqueue(key: str = "k1", **over) -> OutboundEmail:
    email, _ = MailerService.enqueue(
        to_email=over.pop("to_email", "a@example.com"),
        template=over.pop("template", "t"),
        subject=over.pop("subject", "s"),
        idempotency_key=key,
        **over,
    )
    return email


class EnqueueTests(TestCase):
    def test_enqueue_creates_pending_and_normalizes(self) -> None:
        email, created = MailerService.enqueue(
            to_email="A@Example.com", template="t", subject="s", props={"x": 1}, idempotency_key="k1"
        )
        self.assertTrue(created)
        self.assertEqual(email.state, EmailState.PENDING.value)
        self.assertEqual(email.to_email, "a@example.com")
        self.assertEqual(email.attempts, 0)
        self.assertEqual(email.props, {"x": 1})

    def test_enqueue_is_idempotent_on_key(self) -> None:
        _enqueue("dup")
        _email, created = MailerService.enqueue(
            to_email="a@example.com", template="t", subject="s", idempotency_key="dup"
        )
        self.assertFalse(created)
        self.assertEqual(OutboundEmail.objects.count(), 1)


class ClaimDueTests(TestCase):
    def test_claim_flips_to_sending_and_increments_attempts(self) -> None:
        _enqueue()
        claimed = list(MailerService.claim_due())
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0].state, EmailState.SENDING.value)
        self.assertEqual(claimed[0].attempts, 1)

    def test_future_scheduled_is_not_due(self) -> None:
        _enqueue(scheduled_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(list(MailerService.claim_due()), [])

    @override_settings(EMAIL_SEND_MAX_ATTEMPTS=1)
    def test_attempts_cap_excludes_from_claim(self) -> None:
        email = _enqueue()
        OutboundEmail.objects.filter(id=email.id).update(attempts=1)
        self.assertEqual(list(MailerService.claim_due()), [])

    def test_limit_claims_no_more_than_limit(self) -> None:
        for k in ("a", "b", "c"):
            _enqueue(k)
        claimed = list(MailerService.claim_due(limit=2))
        self.assertEqual(len(claimed), 2)
        # The third is left untouched — NOT claimed, no attempt burned (the limit
        # check is before the CAS, so a bounded pass can't orphan a row).
        leftover = OutboundEmail.objects.filter(state=EmailState.PENDING.value)
        self.assertEqual(leftover.count(), 1)
        self.assertEqual(leftover.get().attempts, 0)


@override_settings(EMAIL_SEND_MAX_ATTEMPTS=2, EMAIL_SEND_RETRY_SECONDS=300, EMAIL_SEND_STALE_SECONDS=300)
class CompleteFailReapTests(TestCase):
    def _claimed(self, key: str = "k") -> OutboundEmail:
        _enqueue(key)
        return next(iter(MailerService.claim_due()))

    def test_complete_sent(self) -> None:
        email = self._claimed()
        self.assertTrue(MailerService.complete_sent(email))
        email.refresh_from_db()
        self.assertEqual(email.state, EmailState.SENT.value)
        self.assertIsNotNone(email.sent_at)

    def test_complete_sent_loses_claim_is_noop(self) -> None:
        # The guarded CAS: a stale worker (holding attempts=1) must NOT win after
        # the row was reaped + re-claimed underneath it (attempts bumped). This
        # is what stops a stale completer from clobbering the fresh claim.
        email = self._claimed()  # SENDING, attempts == 1
        OutboundEmail.objects.filter(id=email.id).update(attempts=2)  # reap + reclaim
        self.assertFalse(MailerService.complete_sent(email))  # email still holds attempts=1
        email.refresh_from_db()
        self.assertEqual(email.state, EmailState.SENDING.value)  # unchanged, not SENT
        self.assertIsNone(email.sent_at)

    def test_fail_retries_under_cap_then_fails_at_cap(self) -> None:
        email = self._claimed()  # attempts == 1
        self.assertTrue(MailerService.fail(email, error="boom"))
        email.refresh_from_db()
        self.assertEqual(email.state, EmailState.PENDING.value)  # 1 < 2 -> retry
        self.assertGreater(email.scheduled_at, timezone.now())  # backed off

        # Force it due again, re-claim (attempts -> 2 == cap), fail -> FAILED.
        OutboundEmail.objects.filter(id=email.id).update(scheduled_at=timezone.now())
        email2 = next(iter(MailerService.claim_due()))
        self.assertEqual(email2.attempts, 2)
        MailerService.fail(email2, error="boom again")
        email2.refresh_from_db()
        self.assertEqual(email2.state, EmailState.FAILED.value)

    def test_reap_stale_under_cap_returns_to_pending(self) -> None:
        email = self._claimed()  # SENDING, attempts=1 (< cap 2)
        OutboundEmail.objects.filter(id=email.id).update(started_at=timezone.now() - timedelta(seconds=10_000))
        self.assertEqual(MailerService.reap_stale(), 1)
        email.refresh_from_db()
        self.assertEqual(email.state, EmailState.PENDING.value)

    def test_reap_stale_at_cap_marks_failed(self) -> None:
        # Crash on the FINAL attempt: SENDING at attempts == cap. The reaper must
        # mark FAILED, not bounce to PENDING (where claim_due's attempts<cap
        # filter would strand it forever, never re-sent and never FAILED).
        email = self._claimed()  # attempts=1
        MailerService.fail(email, error="x")  # -> PENDING, attempts=1, backed off
        OutboundEmail.objects.filter(id=email.id).update(scheduled_at=timezone.now())
        email = next(iter(MailerService.claim_due()))  # attempts=2 == cap, SENDING
        self.assertEqual(email.attempts, 2)
        OutboundEmail.objects.filter(id=email.id).update(started_at=timezone.now() - timedelta(seconds=10_000))
        self.assertEqual(MailerService.reap_stale(), 1)
        email.refresh_from_db()
        self.assertEqual(email.state, EmailState.FAILED.value)


@override_settings(CACHES=_LOCMEM)
class DrainCommandTests(TestCase):
    @mock.patch(_SEND)
    def test_drain_sends_pending(self, send: mock.Mock) -> None:
        _enqueue(template="waitlistWelcome")
        call_command("send_outbound_emails")
        send.assert_called_once()
        self.assertEqual(OutboundEmail.objects.get().state, EmailState.SENT.value)

    @mock.patch(_SEND, side_effect=Exception("smtp down"))
    def test_drain_failure_schedules_retry(self, _send: mock.Mock) -> None:
        _enqueue()
        call_command("send_outbound_emails")
        email = OutboundEmail.objects.get()
        self.assertEqual(email.state, EmailState.PENDING.value)  # under default cap (5)
        self.assertEqual(email.attempts, 1)
        self.assertGreater(email.scheduled_at, timezone.now())

    @mock.patch(_SEND)
    def test_dry_run_neither_sends_nor_claims(self, send: mock.Mock) -> None:
        _enqueue()
        call_command("send_outbound_emails", dry_run=True)
        send.assert_not_called()
        email = OutboundEmail.objects.get()
        self.assertEqual(email.state, EmailState.PENDING.value)
        self.assertEqual(email.attempts, 0)

    @mock.patch("mailer.services.MailerService.complete_sent", side_effect=Exception("db blip"))
    @mock.patch(_SEND)
    def test_send_ok_but_complete_fails_does_not_requeue(self, send: mock.Mock, _complete: mock.Mock) -> None:
        # Send succeeds (SMTP accepted) but the complete_sent bookkeeping raises.
        # The row must be LEFT SENDING for the reaper, never requeued to PENDING
        # (which would re-send an already-delivered email).
        _enqueue()
        call_command("send_outbound_emails")
        send.assert_called_once()
        email = OutboundEmail.objects.get()
        self.assertEqual(email.state, EmailState.SENDING.value)
