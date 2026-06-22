"""Account service.

Cross-tenant operations live under `AccountService.Global`; per-account
operations on `AccountService(account_id=...)` instances.
"""

from __future__ import annotations

from accounts.models.account import Account


class AccountGlobal:
    @staticmethod
    def create(*, name: str) -> Account:
        return Account.objects.create(name=name)

    @staticmethod
    def get(id: str) -> Account:
        """Raises Account.DoesNotExist if missing."""
        return Account.objects.get(id=id)

    @staticmethod
    def count() -> int:
        """Total accounts on this instance (telemetry gauge: solo self-host vs
        multi-account)."""
        return Account.objects.count()

    @staticmethod
    def primary_account_id_for(*, user_id: str) -> str | None:
        """Return the user's primary account_id (or any active one as
        fallback). System-level because we don't have an account context
        yet, used by /me responses, signup confirmation, etc.
        """
        from .profiles import UserProfileGlobal

        profile = UserProfileGlobal.primary_for_user(user_id=user_id) or UserProfileGlobal.any_active_for_user(
            user_id=user_id
        )
        return str(profile.account_id) if profile is not None else None


class AccountService:
    """Account-scoped service for the bound account. Stub for now, add
    instance methods as scoped read/write needs appear.
    """

    Global = AccountGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("AccountService requires account_id")
        self.account_id = account_id

    def get(self) -> Account:
        return AccountGlobal.get(self.account_id)
