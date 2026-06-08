"""Outbound-email lifecycle enum (shared, zero-Django).

The Python-side source of truth for an `OutboundEmail` row's `state`. Lives here
so the server validates and branches against one set of values; the server's
`mailer.constants` re-exports it. The DB column stays a bare CharField (no
`choices=`), so adding a value never forces a migration.
"""

from enum import StrEnum


class EmailState(StrEnum):
    """Lifecycle of a queued transactional email.

    - PENDING: enqueued, awaiting (or eligible for) a send attempt.
    - SENDING: claimed by a drain worker, send in flight.
    - SENT:    delivered to the SMTP backend.
    - FAILED:  exhausted the retry budget; needs a human.
    """

    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
