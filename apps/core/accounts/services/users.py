"""User service.

System-level operations under `UserService.Global` (no account context
needed, User isn't account-scoped; users join accounts via UserProfile).
"""

from __future__ import annotations

from accounts.models.user import User


class UserGlobal:
    @staticmethod
    def create(*, email: str, password: str) -> User:
        """Create an active user. Raises if email already exists (uniqueness
        is enforced at the DB level; callers should pre-check via
        `email_exists` if they want a clean validation error)."""
        return User.objects.create_user(email=email, password=password)

    @staticmethod
    def email_exists(email: str) -> bool:
        # iexact: pair with the Lower(email) unique constraint so case
        # differences don't slip through ("Alice@x.com" matches an
        # existing "alice@x.com").
        return User.objects.filter(email__iexact=email).exists()

    @staticmethod
    def get_by_email(email: str) -> User:
        """Raises User.DoesNotExist if missing. Case-insensitive."""
        return User.objects.get(email__iexact=email)

    @staticmethod
    def get(id: str) -> User:
        """Raises User.DoesNotExist if missing."""
        return User.objects.get(id=id)


class UserService:
    """Account-scoped service for user reads/writes. Currently a stub,
    no scoped operations defined yet; add as scoped needs appear.
    """

    Global = UserGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("UserService requires account_id")
        self.account_id = account_id
