"""Waitlist lifecycle enum (shared, zero-Django).

The Python-side source of truth for a waitlist signup's `state`. Lives here
(not server-only) so the server validates and branches against one set of
values. The server's `waitlist.constants` re-exports it ; the DB column stays
a bare CharField (no `choices=`), so adding a value never forces a migration.
"""

from enum import StrEnum


class WaitlistState(StrEnum):
    """The PERSON's lifecycle on the waitlist (single opt-in) — their
    invite-eligibility, NOT email delivery. Delivery of any given email lives on
    `OutboundEmail.state` (mailer); the two are independent. The welcome email
    does not move this state — a fresh signup stays PENDING with its welcome
    already sent. PENDING -> INVITED is a deliberate rollout decision, never
    inferred from email rows (an email being sent says nothing about whether
    you've decided to invite someone).
    """

    # On the list, but NOT yet sent the official early-access invite.
    PENDING = "pending"
    # The official early-access invite has been sent (a deliberate rollout
    # decision). Set by the early-access invite flow (added in a later change);
    # stays INVITED even if that invite email later fails to deliver (the
    # failure shows on the OutboundEmail row, not here).
    INVITED = "invited"
    # Opted out — never email.
    UNSUBSCRIBED = "unsubscribed"
