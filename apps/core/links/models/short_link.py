from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel

from ..constants import CODE_MAX_LENGTH


class ShortLink(BaseModel):
    """A minted short link: a unique `code` that 302-redirects to `url`.

    An ops utility, not a per-tenant product feature: links are created by the
    `create_link` management command with no request user, so (like `waitlist`)
    there is no `account_id` / `user_id` scoping. `code` is the wire identifier
    (the slug in the short URL), separate from the inherited ULID pk. Per-click
    stats are derived from `ClickEvent`, so no denormalized count lives here.
    """

    # unique=True already creates the index on `code`; no separate db_index.
    code = models.CharField(_("code"), max_length=CODE_MAX_LENGTH, unique=True)
    url = models.TextField(_("destination url"))

    class Meta:
        verbose_name = _("short link")
        verbose_name_plural = _("short links")

    def __str__(self) -> str:
        return f"{self.code} -> {self.url[:60]} ({self.id})"
