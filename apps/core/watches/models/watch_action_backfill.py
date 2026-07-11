from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import KIND_MAX_LENGTH, BaseModel
from watches.constants import WatchActionBackfillState


class WatchActionBackfill(BaseModel):
    """A queued request to re-run one action over the previous step's passes
    (the `magpie backfill` flow).

    The POST writes this row (PENDING) and returns fast; the
    `process_due_backfills` cron claims it, does the heavy select/delete/enqueue,
    and marks it terminal. So this table IS the backfill queue (like the PENDING
    WatchActionRun rows are the run queue), keeping the request off the hot path.

    `target_action_id` is the action to re-run; `source_action_id` is the step
    whose SUCCEEDED passes seed it (empty when the target is the chain head, i.e.
    `source_is_head`, in which case the source is the watch's feed items, resolved live).
    The four `*_at` window bounds are the ABSOLUTE datetimes the endpoint resolved
    from the raw request values and pinned here, so a relative window (`90d`)
    doesn't drift between submit and the cron picking the job up.

    Default is additive (fill only items the target never processed); `replace`
    also redoes items already done. Because replacing an action's output makes every
    DOWNSTREAM action's output (derived from it) stale, `replace` regenerates the
    whole chain from the target down: it deletes the target's AND every downstream
    action's terminal runs for the matched items, then re-enqueues the target and
    lets the chain-advance refill downstream. `replace_deleted_at` is the DELETE-ONCE
    marker: the processor stamps it when the delete phase completes, BEFORE enqueuing,
    so a reaped/retried job skips the delete and can't wipe runs the drain has since
    regenerated. The counts are filled by the processor as it runs.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)
    target_action_id = models.CharField(_("target action id"), max_length=26)
    # Empty when the target is the chain head (source_is_head): the source is then
    # the watch's feed items in the window, not an upstream action's runs.
    source_action_id = models.CharField(_("source action id"), max_length=26, blank=True, default="")
    source_is_head = models.BooleanField(_("source is head"), default=False)
    # Denormalized target kind (like WatchActionRun.kind), so the job (and its wire
    # shape) still render if the action is later deleted. The processor enqueues runs
    # with the action's LIVE kind, not this pin, so a mid-flight kind edit doesn't
    # matter; this is purely for rendering a job whose action is gone. Holds a built-in
    # WatchActionKind OR a registered plugin kind (extensible union); help_text still
    # names WatchActionKind and is left as-is to avoid a DB-no-op AlterField migration.
    kind = models.CharField(
        _("kind"), max_length=KIND_MAX_LENGTH, default="", help_text=_("target WatchActionKind value")
    )
    # replace=False -> additive (fill only never-processed items). replace=True ->
    # regenerate the whole chain from the target down for the matched items.
    replace = models.BooleanField(_("replace"), default=False)
    # Resolved absolute window bounds (null = unbounded that side). occurred_* on the
    # feed item's source time, completed_* on the source run's completion.
    occurred_since = models.DateTimeField(_("occurred since"), null=True, blank=True)
    occurred_until = models.DateTimeField(_("occurred until"), null=True, blank=True)
    completed_since = models.DateTimeField(_("completed since"), null=True, blank=True)
    completed_until = models.DateTimeField(_("completed until"), null=True, blank=True)
    state = models.CharField(
        _("state"),
        max_length=16,
        default=WatchActionBackfillState.PENDING.value,
        help_text=_("WatchActionBackfillState value"),
    )
    # Progress, filled by the processor: matched source passes in the window;
    # present/pruned split by whether the feed item still exists; deleted terminal
    # runs (target + downstream when replace); enqueued fresh target runs. Best-effort,
    # NOT exact under a reap+retry: a reclaimed pass recomputes them and enqueued
    # counts only rows it created this pass (prior-pass rows are idempotently skipped).
    matched = models.PositiveIntegerField(_("matched"), default=0)
    present = models.PositiveIntegerField(_("present"), default=0)
    pruned = models.PositiveIntegerField(_("pruned"), default=0)
    deleted = models.PositiveIntegerField(_("deleted"), default=0)
    enqueued = models.PositiveIntegerField(_("enqueued"), default=0)
    # Delete-once marker: stamped when the delete phase finishes, BEFORE the target
    # enqueue, so a retried job never re-deletes drain-regenerated runs.
    replace_deleted_at = models.DateTimeField(_("replace deleted at"), null=True, blank=True)
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    error = models.TextField(_("error"), blank=True, default="")
    # Claim attempts (the cron's CAS increments on each claim); the reaper resets a
    # stale RUNNING to FAILED (retryable, completed_at cleared, so claim_due re-picks
    # it since FAILED is claimable), and once attempts hit the cap the job stays FAILED.
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)

    class Meta:
        verbose_name = _("watch action backfill")
        verbose_name_plural = _("watch action backfills")
        indexes = [
            # The cron claims due jobs ordered by schedule; DELIBERATELY account-
            # agnostic (a Global cross-tenant scan, like watchrun_state_sched_idx).
            models.Index(fields=["state", "scheduled_at"], name="watchbackfill_state_sched_idx"),
            # This account's backfills, newest-first (status readback / a future list).
            models.Index(fields=["account_id", "id"], name="watchbackfill_acct_id_idx"),
        ]

    def __str__(self) -> str:
        return f"backfill {self.target_action_id} [{self.state}] ({self.id})"
