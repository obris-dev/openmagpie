"""Waitlist enums (shared, zero-Django).

The Python-side source of truth for a waitlist signup's `state` and `category`.
Lives here (not server-only) so the server validates and branches against one
set of values. The server's `waitlist.constants` re-exports them ; the DB
columns stay bare CharFields (no `choices=`), so adding a value never forces a
migration.
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


class WaitlistCategory(StrEnum):
    """What the signup is WAITING FOR — the product shape they want, captured
    as an optional second step on the marketing confirmation card. Today the
    functional interface is the CLI, so this distinguishes who wants to keep
    running it themselves (just give them a UI) from who wants us to run it.

    Orthogonal to `state` (lifecycle) and `source` (which form converted).
    Captured AFTER the email (a delayed second step), so a fresh signup is
    UNKNOWN — the column default — until the person picks, or forever if they
    never do. UNKNOWN is a real member (not the empty string) so "not asked
    yet" is an explicit, queryable value.
    """

    # Default: on the list, but hasn't told us which shape they want.
    UNKNOWN = "unknown"
    # Will self-host — waiting on a web UI so they're not living in the CLI.
    WEB_UI = "web_ui"
    # Doesn't want to run infra — waiting on the managed cloud version.
    CLOUD = "cloud"
    # Happy with whichever ships first; no preference between the two.
    EITHER = "either"
