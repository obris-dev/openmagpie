"""Shared auth identity shapes.

Zero-Django wire models for the authenticated user, shared by core (server),
the magpie CLI, and the web client (which generates its validator from the
contract). The token / device-session / cli-token shapes stay client-specific
(the browser deliberately never sees raw tokens or the device `user_code`), so
only the cross-client identity lives here.
"""

from datetime import datetime

from pydantic import BaseModel


class AuthUser(BaseModel):
    """The authenticated user returned by `/v1/auth` (me / signup / login, and
    the CLI device-session completed bag).

    `account_id` is REQUIRED: a user belongs to an account (signup creates one
    and binds the user), so a user response without an account is an invariant
    violation, not a valid state. `created_at` is the user's join time."""

    id: str
    email: str
    account_id: str
    created_at: datetime
