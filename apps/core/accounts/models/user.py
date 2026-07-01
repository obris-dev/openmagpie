from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import EmailValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from common.fields import ULIDField


class UserManager(BaseUserManager["User"]):
    """Custom user manager for email-based authentication."""

    def create_user(self, email: str, password: str | None = None, **extra_fields: object) -> "User":
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str | None = None, account_id: str | None = None, **extra_fields: object
    ) -> "User":
        # Every user must belong to an account (AuthUser.account_id is non-null, and
        # /v1/auth/me + login raise on a user with none). createsuperuser bypasses
        # signup, so REQUIRE the caller to name the account to bind, rather than
        # silently minting one: an account-less admin would 500 on /me. Nothing
        # bootstraps a superuser today, so callers pass account_id explicitly (and
        # fail loud here if they forget).
        if not account_id:
            raise ValueError("account_id is required to create a superuser (every user must belong to an account)")
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # Lazy import: accounts.services imports models, so a module-level import
        # here would be circular. Atomic so a bind failure can't leave an orphaned admin.
        from django.db import transaction

        from accounts.services import AccountService, UserProfileService

        with transaction.atomic():
            # Fail loud on a WRONG account_id (a typo, a nonexistent id) too, not
            # just a missing one: bind_owner would otherwise create an owner profile
            # pointing at no account. Raises Account.DoesNotExist.
            AccountService.Global.get(account_id)
            user = self.create_user(email, password, **extra_fields)
            UserProfileService.Global.bind_owner(user_id=str(user.id), account_id=account_id)
        return user

    def get_by_natural_key(self, username: str | None):
        """Case-insensitive email lookup for `authenticate()`.

        Django's ModelBackend uses this to resolve the USERNAME_FIELD
        ("email"). The default impl does a case-SENSITIVE compare, which
        on SQLite means `Alice@x.com` and `alice@x.com` would be treated
        as different users at login time. iexact + the Lower(email)
        unique constraint keep email handling case-insensitive end-to-end.
        """
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractUser):
    """Custom user model that uses email as the username field."""

    id = ULIDField(primary_key=True)
    email = models.EmailField(
        _("email address"),
        # `unique=True` is required by Django's auth.E003 check (the
        # USERNAME_FIELD must be unique). On SQLite this is case-
        # sensitive at the column level, which is why we ALSO add the
        # case-insensitive `Lower(email)` constraint below, the field-
        # level catches exact duplicates, the expression constraint
        # catches mixed-case ones.
        unique=True,
        validators=[EmailValidator()],
        help_text=_("Email address of the user"),
    )
    username = None  # Disable username field, email is the login

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        constraints = [
            # Case-insensitive uniqueness. Without this, SQLite's
            # case-sensitive `=` would let a manage.py shell user insert
            # both `alice@x.com` and `Alice@x.com` and we'd treat them
            # as two distinct accounts.
            models.UniqueConstraint(Lower("email"), name="unique_lower_email"),
        ]

    def __str__(self) -> str:
        return str(self.email)
