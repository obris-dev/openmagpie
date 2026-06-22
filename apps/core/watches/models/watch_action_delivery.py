from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel
from watches.constants import WatchActionDeliveryState


class WatchActionDelivery(BaseModel):
    """One outbound HTTP call ATTEMPT made by a delivery action ; the
    physical-call audit row, sibling to WatchActionRun (the logical per-item
    execution).

    A digest window that fails and retries makes several calls, so this is
    1:N under a window (one row per attempt) ; that per-attempt history is the
    point of the log. The owning runs link back via `WatchActionRun.delivery_id`
    (set when the call lands), so "which runs did this call carry" is a query
    on that column and "which call carried this run" is the run's pointer.

    Retry is NOT driven here: the run state owns requeue. This row only RECORDS
    each attempt. Delivery is at-least-once and receivers dedup per item on the
    in-body `key`, so there is no server-side dedup field.

    `request_payload` is the exact body we sent (the WebhookPayload), stored
    point-in-time so the log is faithful even after the items are pruned or the
    config changes. Headers are NEVER stored (they carry auth tokens) ; the
    body carries no secrets. `target_host` is the bare destination host (no
    path/query, which can carry a token).

    GROWTH: append-only, one row per HTTP attempt (every outcome), each holding
    the full batch body in `request_payload`. So storage grows with delivery
    volume x batch size x retries. No retention/pruning yet ; it shares the
    deferred ActionRun-retention story (post-v1) and would prune oldest-first by
    the ULID pk per action."""

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)
    action_id = models.CharField(_("action id"), max_length=26)
    # The cadence this call delivered under (instant | digest).
    delivery = models.CharField(_("delivery"), max_length=16)
    # The HTTP verb used (WebhookMethod value).
    method = models.CharField(_("method"), max_length=8)
    # Redacted destination host (never the full URL or any secret).
    target_host = models.CharField(_("target host"), max_length=255, blank=True, default="")
    state = models.CharField(
        _("state"),
        max_length=16,
        default=WatchActionDeliveryState.PENDING.value,
        help_text=_("WatchActionDeliveryState value"),
    )
    # The HTTP status of the response ; null until the call returns.
    http_status = models.PositiveSmallIntegerField(_("http status"), null=True, blank=True)
    # How many items this call carried (1 for instant, N for a digest batch).
    item_count = models.PositiveIntegerField(_("item count"), default=0)
    # Which attempt this was for the owning run(s) (carried from the run).
    attempt = models.PositiveSmallIntegerField(_("attempt"), default=0)
    # The exact body sent (WebhookPayload dump). Headers are NOT stored.
    request_payload = models.JSONField(
        _("request payload"), default=dict, help_text=_("The WebhookPayload sent (no headers)")
    )
    error = models.TextField(_("error"), blank=True, default="")
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)

    class Meta:
        verbose_name = _("watch action delivery")
        verbose_name_plural = _("watch action deliveries")
        indexes = [
            # Per-action audit log (magpie watch action deliveries <id>),
            # newest-first by ULID pk. account_id-first for scoping.
            models.Index(fields=["account_id", "action_id", "id"], name="watchdeliv_acct_action_idx"),
            # Telemetry heartbeat rollup (count_since): cross-tenant count over a
            # created_at window. DELIBERATELY account-agnostic (a Global scan).
            models.Index(fields=["created_at"], name="watchdeliv_created_idx"),
        ]

    def __str__(self) -> str:
        return f"delivery {self.action_id} {self.method} [{self.state}] ({self.id})"
