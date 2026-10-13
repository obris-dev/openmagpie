from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel
from feeds.configs import CuratedFeedConfig

# Floor for Feed.poll_interval_seconds. Reddit's anonymous JSON endpoint
# rate-limits below ~60s/req; tighter cadences also burn fetch budget for no
# real signal gain. (The Feed owns the poll cadence.)
MIN_POLL_INTERVAL_SECONDS = 60


class Feed(BaseModel):
    """A curated set of Source rows the feed polls on a cadence.

    Each `feeds.Source` row owns its own `last_event_at` watermark; the
    Feed row carries the schedule + kind-specific config in `data`
    (validated by the Pydantic class registered for `kind` in
    `feeds.registry`). Every polled item is persisted as a FeedItem
    (the browsable "sort by new" log). Watches subscribe to a Feed
    and judge its items.
    """

    user_id = models.CharField(_("user id"), max_length=26)
    account_id = models.CharField(_("account id"), max_length=26)
    kind = models.CharField(
        _("kind"),
        max_length=32,
        default=CuratedFeedConfig.FEED_KIND,
        help_text=_("Feed kind, identifies the Pydantic config class in feeds.registry"),
    )
    name = models.CharField(_("name"), max_length=255, help_text=_("Short label"))
    is_active = models.BooleanField(_("is active"), default=True)

    # Feed-level SCHEDULING (when the feed runs). Per-source DATA
    # position (last_event_at) lives per-source on the Source row, NOT
    # here.
    poll_interval_seconds = models.PositiveIntegerField(
        _("poll interval seconds"),
        default=300,
        validators=[MinValueValidator(MIN_POLL_INTERVAL_SECONDS)],
    )
    last_polled_at = models.DateTimeField(_("last polled at"), null=True, blank=True)
    next_poll_at = models.DateTimeField(_("next poll at"), null=True, blank=True)

    data = models.JSONField(
        _("data"),
        default=dict,
        help_text=_("Typed config blob (streams + retention), validated per `kind` (see feeds.registry)"),
    )

    class Meta:
        verbose_name = _("feed")
        verbose_name_plural = _("feeds")
        indexes = [
            models.Index(fields=["account_id"]),
            models.Index(fields=["is_active", "next_poll_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"
