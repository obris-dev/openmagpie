from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import KIND_MAX_LENGTH, BaseModel


class Source(BaseModel):
    """One place a Feed pulls data from.

    `feed_id` and `account_id` are plain CharFields, not FKs ; service
    layer owns cascades (mirrors `FeedItem`). `kind` is denormalized
    from the JSONB spec so queries like "all rss sources" can hit an
    index instead of a JSONB path expression. `spec_hash` is the
    sha256 of the canonical spec JSON, used purely as the dedup key
    for the unique constraint.

    Per-source watermark (`last_event_at`) is a column so polling
    advances are O(1) per row.

    `meta` is operator-supplied free-form tags that the recorder
    copies onto each FeedItem the source produces. `field_map` is
    connector-readable hints; empty means inherit the feed's
    `default_field_map`. Keys a connector doesn't recognise are
    ignored.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    feed_id = models.CharField(_("feed id"), max_length=26)
    kind = models.CharField(
        _("kind"),
        max_length=KIND_MAX_LENGTH,
        help_text=_("Denormalized from spec; matches the SourceSpec discriminator"),
    )
    spec = models.JSONField(
        _("spec"),
        help_text=_("Full SourceSpec dump (includes kind for round-trip via the discriminated union)"),
    )
    spec_hash = models.CharField(
        _("spec hash"),
        max_length=64,
        help_text=_("sha256 of the canonical spec dump; unique within (account_id, feed_id)"),
    )
    last_event_at = models.DateTimeField(_("last event at"), null=True, blank=True)
    meta = models.JSONField(
        _("meta"),
        default=dict,
        blank=True,
        help_text=_("Operator-supplied tags; copied onto each FeedItem this source produces"),
    )
    field_map = models.JSONField(
        _("field map"),
        default=dict,
        blank=True,
        help_text=_("Connector-readable hints; overrides the feed's default_field_map per key"),
    )

    class Meta:
        verbose_name = _("source")
        verbose_name_plural = _("sources")
        constraints = [
            models.UniqueConstraint(
                fields=["account_id", "feed_id", "spec_hash"],
                name="uniq_account_feed_spec_hash",
            ),
        ]
        indexes = [
            # Name pinned to match what 0001_initial declared so
            # `makemigrations --check` stays clean. Without an explicit
            # name Django auto-generates a per-model hash that drifts
            # from migration history on any subsequent run.
            models.Index(fields=["account_id", "feed_id", "id"], name="feeds_sourc_acct_feed_id_idx"),
            # No standalone `kind` index: the per-feed kind queries (poll's
            # `iter_by_kind` / `iter_for_poll`) ride the (account_id, feed_id)
            # prefix above and filter `kind` on that already-narrow per-feed set.
            # Add one only if a cross-feed "all sources of kind X" query lands.
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.spec_hash[:8]} ({self.id})"
