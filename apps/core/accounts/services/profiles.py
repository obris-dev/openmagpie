"""UserProfile service.

Profiles are the User ↔ Account link with role + status. Scoped service
operates within one account; Global handles cross-tenant lookups used
during signup and identity resolution.
"""

from __future__ import annotations

from accounts.constants import (
    PROFILE_ROLE_OWNER,
    PROFILE_STATUS_ACTIVE,
)
from accounts.models.account import UserProfile


class UserProfileGlobal:
    @staticmethod
    def bind_owner(*, user_id: str, account_id: str) -> UserProfile:
        """Create the primary owner profile for a newly-signed-up user.
        Used during signup, where the user is the only member of the
        account they're joining.
        """
        return UserProfile.objects.create(
            user_id=user_id,
            account_id=account_id,
            is_primary=True,
            status=PROFILE_STATUS_ACTIVE,
            role=PROFILE_ROLE_OWNER,
        )

    @staticmethod
    def primary_for_user(*, user_id: str) -> UserProfile | None:
        # Order by id (ULID, monotonically sortable by creation time) so
        # a user with multiple matching profiles always gets the same
        # one back, the first-created. Without an explicit order, the
        # DB is free to return any row and the choice can flip between
        # queries.
        return UserProfile.objects.filter(user_id=user_id, is_primary=True).order_by("id").first()

    @staticmethod
    def any_active_for_user(*, user_id: str) -> UserProfile | None:
        return UserProfile.objects.filter(user_id=user_id, status=PROFILE_STATUS_ACTIVE).order_by("id").first()

    @staticmethod
    def is_active_owner(*, user_id: str, account_id: str) -> bool:
        """Whether the user is an ACTIVE owner of the GIVEN account -- a pure
        per-account ownership predicate (status + role, NOT the is_primary flag, so
        the caller decides which account). Backs operator gates like telemetry,
        where the caller resolves the acting account the standard way. A revoked or
        pending owner does NOT qualify."""
        return UserProfile.objects.filter(
            user_id=user_id,
            account_id=account_id,
            status=PROFILE_STATUS_ACTIVE,
            role=PROFILE_ROLE_OWNER,
        ).exists()


class UserProfileService:
    """Account-scoped service for the bound account's profiles. Stub for
    now, add instance methods as scoped read/write needs appear.
    """

    Global = UserProfileGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("UserProfileService requires account_id")
        self.account_id = account_id
