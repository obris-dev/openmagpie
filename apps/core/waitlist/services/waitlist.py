"""Waitlist signup + invite operations.

Not account-scoped: signups are captured from the public marketing site before
any account exists. Static methods only (there's no per-tenant state to carry).
"""

import logging
from collections.abc import Iterator

from django.conf import settings
from django.utils import timezone

from common.email import EmailService

from ..constants import WaitlistState
from ..models import WaitlistSignup

logger = logging.getLogger(__name__)


class WaitlistService:
    """Public waitlist: signup (idempotent on email) + invite lifecycle."""

    @staticmethod
    def signup(*, email: str, source: str = "") -> tuple[WaitlistSignup, bool]:
        """Add an email to the waitlist. Idempotent: returns (signup, created).

        Re-submitting an existing address is a no-op (no duplicate, no second
        welcome email). The welcome email is best-effort: a render/SMTP outage
        is logged but never fails the signup.
        """
        normalized = email.strip().lower()
        signup, created = WaitlistSignup.objects.get_or_create(
            email=normalized,
            defaults={"source": source},
        )
        if created:
            WaitlistService._send_welcome(signup)
        return signup, created

    @staticmethod
    def _send_welcome(signup: WaitlistSignup) -> None:
        """Best-effort welcome email; never raises into the signup path.

        Catches broadly on purpose: signup must succeed even if rendering or
        sending blows up in an unforeseen way (malformed render response,
        misconfigured backend, etc.). The invite path (`mark_invited`) does NOT
        swallow, so a failed invite leaves the row PENDING to retry.
        """
        try:
            EmailService.send_template(
                to_email=signup.email,
                subject="You're on the OpenMagpie waitlist",
                template="waitlistWelcome",
                props={"email": signup.email, "siteUrl": settings.WEB_BASE_URL},
            )
        except Exception:
            # Best-effort: swallow so signup succeeds, but log at ERROR with the
            # full traceback (logger.exception) so the failure isn't lost.
            logger.exception("waitlist welcome email failed for %s", signup.email)

    @staticmethod
    def iter_pending(*, chunk_size: int = 100) -> Iterator[WaitlistSignup]:
        """PENDING signups, oldest first (invite in signup order).

        Ordered by the ULID PK, not created_at: the ULID's high bits are the
        creation timestamp, so `id` is chronological AND indexed (repo rule).
        """
        return (
            WaitlistSignup.objects.filter(state=WaitlistState.PENDING.value)
            .order_by("id")
            .iterator(chunk_size=chunk_size)
        )

    @staticmethod
    def mark_invited(signup: WaitlistSignup) -> WaitlistSignup:
        """Send the early-access invite and flip PENDING -> INVITED.

        The state flip is only persisted after the invite email succeeds, so a
        send failure leaves the row PENDING for the next run to retry.
        """
        EmailService.send_template(
            to_email=signup.email,
            subject="Your OpenMagpie early access is ready",
            template="waitlistInvite",
            props={"email": signup.email, "appUrl": settings.WEB_BASE_URL},
        )
        signup.state = WaitlistState.INVITED.value
        signup.invited_at = timezone.now()
        signup.save(update_fields=["state", "invited_at", "updated_at"])
        return signup
