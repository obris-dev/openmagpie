from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel

from ..constants import COUNTRY_CODE_LENGTH, UNKNOWN_COUNTRY


class ClickEvent(BaseModel):
    """One recorded click event, for deduplicated unique counts + geo. NOT one row
    per raw hit: at most one row per visitor per link per dedup window (see
    CLICK_DEDUP_WINDOW_SECONDS), so a refresh or bot re-fetch inside the window is
    collapsed. The row count is therefore recorded (deduped) events, not raw
    traffic (a bot hitting a link 100 times shows ~1). IP-less clicks (blank
    ip_hash) are the one exception and are recorded every time.

    `ip_hash` is a keyed HMAC of the visitor IP (never the raw IP): distinct
    hashes give the unique-visitor count without persisting PII, matching the
    codebase's IP-averse posture. `country` is Cloudflare's 2-letter code (the
    CF-IPCountry header), so no local GeoIP database is needed. `props` holds
    coarse request context: the user-agent and the referer's origin only
    (scheme://host, path + query stripped so no PII-bearing referer URL is kept).
    `short_link_id` is a ULID char-pointer to the ShortLink (no FK, per the house
    rule); `created_at` (from BaseModel) is the click timestamp.
    """

    # No standalone db_index: the (short_link_id, country) index below leads with
    # short_link_id, so a short_link_id-only lookup uses its leftmost prefix.
    short_link_id = models.CharField(_("short link id"), max_length=26)
    ip_hash = models.CharField(_("ip hash"), max_length=64, blank=True, default="")
    country = models.CharField(_("country"), max_length=COUNTRY_CODE_LENGTH, blank=True, default="")
    props = models.JSONField(_("props"), default=dict)

    class Meta:
        verbose_name = _("click event")
        verbose_name_plural = _("click events")
        indexes = [
            # Leads with short_link_id (covers short_link_id-only lookups too) and
            # includes country for the by-country rollup.
            models.Index(fields=["short_link_id", "country"], name="clickevent_link_country_idx"),
        ]

    def __str__(self) -> str:
        return f"click {self.short_link_id} {self.country or UNKNOWN_COUNTRY} ({self.id})"
