"""Waitlist signup operations.

Not account-scoped: signups are captured from the public marketing site before
any account exists. Static methods only (there's no per-tenant state to carry).
Email is never sent inline here — signup ENQUEUES an OutboundEmail (a cheap
insert) and the mailer drain renders + sends it out-of-request.

The early-access invite flow (PENDING -> INVITED + an `earlyAccessInvite` email)
is not built yet; it lands in a later change.
"""

from common.web_urls import marketing_url
from mailer.services import MailerService

from ..constants import WaitlistCategory
from ..models import WaitlistSignup


class WaitlistService:
    """Public waitlist: idempotent signup + welcome email."""

    @staticmethod
    def signup(
        *,
        email: str,
        source: str = "",
        category: str = WaitlistCategory.UNKNOWN.value,
    ) -> tuple[WaitlistSignup, bool]:
        """Add an email to the waitlist. Idempotent: returns (signup, created).

        On first signup, enqueue a welcome email — idempotent on the signup id,
        so a retried signup can't double-send. The send happens later in the
        mailer drain, so the public request never blocks on render/SMTP.

        `category` is captured as a delayed second step (the confirmation card),
        so it arrives on a SECOND call for an already-created row. A real pick
        (anything but UNKNOWN) is recorded on the existing signup; this never
        re-enqueues the welcome (that's gated on `created`), so the second call
        can't double-send. UNKNOWN is a no-op, so the email-only first call (or
        any later re-submit) never clobbers a pick already on record.
        """
        normalized = email.strip().lower()
        signup, created = WaitlistSignup.objects.get_or_create(
            email=normalized,
            defaults={"source": source, "category": category},
        )
        if created:
            MailerService.enqueue(
                to_email=signup.email,
                subject="You're on the OpenMagpie waitlist",
                template="waitlistWelcome",
                props={"siteUrl": marketing_url()},
                idempotency_key=f"waitlist-welcome:{signup.id}",
            )
        elif category != WaitlistCategory.UNKNOWN.value and signup.category != category:
            signup.category = category
            signup.save(update_fields=["category", "updated_at"])
        return signup, created
