from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import KIND_MAX_LENGTH, BaseModel


class WatchAction(BaseModel):
    """One node in a watch path's linear chain ; a filter or a delivery.

    ONE table, kind-discriminated (see `watches.constants.WatchActionKind`):
    semantic_filter (gates the chain), webhook / log (deliver outward).
    Delivery cadence (instant vs digest) is a field in a delivery action's
    `config`, not its own kind. `config` is the kind-specific blob,
    validated server-side by a kind-keyed Pydantic registry (later
    commit) ; opaque here.

    Ordering is a dense integer `rank` (0..N-1, contiguous) WITHIN a
    path, unique on `(path_id, rank)`. Chain entry = `rank == 0`; "next"
    = `rank + 1` (plain SQL sort, no traversal). Insert/move/delete
    renumber the affected rows in a transaction. A dense sortable column
    also renders the chain for the future flow UI. `kind` is a bare
    CharField (no `choices=`) so a new kind needs no migration.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    path_id = models.CharField(_("path id"), max_length=26)
    # The column now legitimately holds a built-in WatchActionKind OR a registered plugin
    # kind (the union is extensible). help_text still names WatchActionKind (the built-in
    # case) and is left as-is on purpose: rewording it would generate a DB-no-op AlterField
    # migration, which this change deliberately avoids.
    kind = models.CharField(
        _("kind"), max_length=KIND_MAX_LENGTH, help_text=_("WatchActionKind value; selects impl + config contract")
    )
    config = models.JSONField(_("config"), default=dict, help_text=_("Kind-specific config blob, validated per kind"))
    rank = models.PositiveIntegerField(_("rank"), help_text=_("Dense 0-based position within the path"))

    class Meta:
        verbose_name = _("watch action")
        verbose_name_plural = _("watch actions")
        constraints = [
            # account_id-first for scoping + index coverage (the scoped
            # "actions in this path" read filters account_id + path_id);
            # (path, rank) is already globally unique.
            models.UniqueConstraint(
                fields=["account_id", "path_id", "rank"],
                name="uniq_watchaction_account_path_rank",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}#{self.rank} ({self.id})"
