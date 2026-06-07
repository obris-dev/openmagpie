"""Waitlist lifecycle enum (shared, zero-Django).

The Python-side source of truth for a waitlist signup's `state`. Lives here
(not server-only) so the server validates and branches against one set of
values. The server's `waitlist.constants` re-exports it ; the DB column stays
a bare CharField (no `choices=`), so adding a value never forces a migration.
"""

from enum import StrEnum


class WaitlistState(StrEnum):
    """Lifecycle of a waitlist signup (single opt-in).

    - PENDING:      on the list, awaiting an early-access invite.
    - INVITED:      early-access invite email sent.
    - UNSUBSCRIBED: opted out ; never email.
    """

    PENDING = "pending"
    INVITED = "invited"
    UNSUBSCRIBED = "unsubscribed"
