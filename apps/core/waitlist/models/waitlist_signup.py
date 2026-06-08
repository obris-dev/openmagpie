from django.contrib.postgres.fields import ArrayField
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
    sent (stamping `invited_at`), or UNSUBSCRIBED on opt-out. `state` and the
    deprecated `category` are bare CharFields (see `waitlist.constants`).
    """

    email = models.EmailField(_("email"), unique=True)
    state = models.CharField(
        _("state"),
        max_length=32,
        default=WaitlistState.PENDING.value,
    )
    # DEPRECATED: superseded by `source_interests`. The web_ui/cloud/either
    # question contradicted the hosted-only marketing CTA, so it's no longer
    # collected. Column retained (not dropped) to preserve any early values.
    category = models.CharField(
        _("category"),
        max_length=32,
        default=WaitlistCategory.UNKNOWN.value,
        help_text=_("Deprecated: superseded by source_interests; no longer collected"),
    )
    # The not-yet-shipped sources they most want (optional MULTI-select vote on
    # the confirmation card). A set, stored as a Postgres array of enum values;
    # empty = no vote. Element values are validated app-side (the serializer), so
    # the column stays choice-free. OTHER pairs with the free text below.
    source_interests = ArrayField(
        models.CharField(max_length=32),
        default=list,
        blank=True,
        help_text=_("Most-wanted roadmap sources (linkedin / slack / ...); empty = no vote"),
    )
    source_interest_other = models.CharField(
        _("source interest (other)"),
        max_length=120,
        blank=True,
        default="",
        help_text=_("Free text when 'other' is among source_interests"),
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
