"""Waitlist signup operations.

Not account-scoped: signups are captured from the public marketing site before
any account exists. Static methods only (there's no per-tenant state to carry).
Email is never sent inline here: signup ENQUEUES an OutboundEmail (a cheap
insert) and the mailer drain renders + sends it out-of-request.

The early-access invite flow (PENDING -> INVITED + an `earlyAccessInvite` email)
is not built yet; it lands in a later change.
"""

from common.web_urls import marketing_url
from mailer.services import MailerService

from ..constants import WaitlistSourceInterest
from ..models import WaitlistSignup

# Canonical vote order: the enum's declared sequence. Storing/comparing in this
# order makes the vote behave as a SET, so re-submitting the same picks in any
# order is a true no-op (no redundant write).
_SOURCE_ORDER = {s.value: i for i, s in enumerate(WaitlistSourceInterest)}


def _canonical(values: list[str]) -> list[str]:
    """De-dup + order by the enum sequence (the vote is a set, not a sequence)."""
    return sorted(set(values), key=lambda v: _SOURCE_ORDER.get(v, len(_SOURCE_ORDER)))


class WaitlistService:
    """Public waitlist: idempotent signup + welcome email."""

    @staticmethod
    def signup(
        *,
        email: str,
        source: str = "",
        source_interests: list[str] | None = None,
        source_interest_other: str = "",
    ) -> tuple[WaitlistSignup, bool]:
        """Add an email to the waitlist. Idempotent: returns (signup, created).

        On first signup, enqueue a welcome email, idempotent on the signup id,
        so a retried signup can't double-send. The send happens later in the
        mailer drain, so the public request never blocks on render/SMTP.

        `source_interests` (the most-wanted-source multi-vote) is captured as a
        delayed second step (the confirmation card), so it arrives on a SECOND
        call for an already-created row. A non-empty vote is recorded on the
        existing signup; this never re-enqueues the welcome (gated on `created`),
        so the second call can't double-send. An empty vote is a no-op, so the
        email-only first call (or any later re-submit) never clobbers a vote
        already on record. The free-text `source_interest_other` is kept only
        when OTHER is among the votes and cleared otherwise.
        """
        normalized = email.strip().lower()
        votes = _canonical(source_interests or [])
        other = source_interest_other if WaitlistSourceInterest.OTHER.value in votes else ""
        signup, created = WaitlistSignup.objects.get_or_create(
            email=normalized,
            defaults={
                "source": source,
                "source_interests": votes,
                "source_interest_other": other,
            },
        )
        if created:
            MailerService.enqueue(
                to_email=signup.email,
                subject="You're on the OpenMagpie waitlist",
                template="waitlistWelcome",
                props={"siteUrl": marketing_url()},
                idempotency_key=f"waitlist-welcome:{signup.id}",
            )
        elif votes and (signup.source_interests != votes or signup.source_interest_other != other):
            signup.source_interests = votes
            signup.source_interest_other = other
            signup.save(
                update_fields=[
                    "source_interests",
                    "source_interest_other",
                    "updated_at",
                ]
            )
        return signup, created
