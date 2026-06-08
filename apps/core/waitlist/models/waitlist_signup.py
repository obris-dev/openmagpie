from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel

from ..constants import WaitlistCategory, WaitlistState


class WaitlistSignup(BaseModel):
    """A public waitlist signup for the hosted / early-access version.

    Pre-account: captured from the marketing site before any user exists, so
    there is no `account_id` / `user_id` scoping. `email` is unique, which makes
    signup idempotent (re-submitting the same address is a no-op). Single
    opt-in: created PENDING, flipped to INVITED when an early-access invite is
    sent (stamping `invited_at`), or UNSUBSCRIBED on opt-out. `state` and
    `category` are bare CharFields (see `waitlist.constants`).
    """

    email = models.EmailField(_("email"), unique=True)
    state = models.CharField(
        _("state"),
        max_length=32,
        default=WaitlistState.PENDING.value,
    )
    category = models.CharField(
        _("category"),
        max_length=32,
        default=WaitlistCategory.UNKNOWN.value,
        help_text=_(
            "What the signup is waiting for (web_ui / cloud / either); UNKNOWN until they pick on the confirmation card"
        ),
    )
    source = models.CharField(
        _("source"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Where the signup came from (e.g. the marketing form id)"),
    )
    invited_at = models.DateTimeField(_("invited at"), null=True, blank=True)

    class Meta:
        verbose_name = _("waitlist signup")
        verbose_name_plural = _("waitlist signups")
        indexes = [
            models.Index(fields=["state"]),
            models.Index(fields=["category"]),
        ]

    def __str__(self) -> str:
        return f"{self.email} ({self.state})"
