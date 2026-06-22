from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import BaseModel
from watches.constants import WatchActionRunState


class WatchActionRun(BaseModel):
    """One execution of one WatchAction against one FeedItem ; the
    stateful audit row of the whole pipeline.

    ONE table, `result` is the kind-specific output blob (opaque here,
    validated per kind by the registry). `state` is a bare CharField
    over `watches.constants.WatchActionRunState` (no `choices=`): the runner
    advances the chain to `rank+1` IFF `state == SUCCEEDED`; `GATED` is
    a clean run whose result halts the chain (a filter pass=false).

    `watch_id` is denormalized for cheap watch-scoped queries; the path
    is reachable via the action. `prior_run_id` records which run queued
    this one (provenance). Idempotent on `(watch, action, feed_item)`.
    """

    account_id = models.CharField(_("account id"), max_length=26)
    watch_id = models.CharField(_("watch id"), max_length=26)
    action_id = models.CharField(_("action id"), max_length=26)
    feed_item_id = models.CharField(_("feed item id"), max_length=26)
    state = models.CharField(
        _("state"),
        max_length=16,
        default=WatchActionRunState.PENDING.value,
        help_text=_("WatchActionRunState value"),
    )
    # When this run becomes relevant (the drain claims scheduled_at <= now).
    # A run is "digest" not by any field here but by its ACTION having a
    # WatchActionDigestWindow ; the drain excludes those and the flush
    # batches them, so digest-ness is the action's property, not the run's.
    scheduled_at = models.DateTimeField(_("scheduled at"), null=True, blank=True)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    completed_at = models.DateTimeField(_("completed at"), null=True, blank=True)
    result = models.JSONField(_("result"), default=dict, help_text=_("Kind-specific result blob"))
    error = models.TextField(_("error"), blank=True, default="")
    # Execution attempts ; incremented on each claim (the drain's CAS).
    # The drain stops claiming a run once attempts reaches
    # WATCH_RUN_MAX_ATTEMPTS, so a persistently-failing or worker-crashing
    # run can't retry forever (it stays terminally FAILED). SmallInteger:
    # the value is single-digit (capped at a few), never large.
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)
    # The run that queued this one (the prior action in the chain). Blank
    # for the chain's first run (queued by the trigger pass, not a run).
    prior_run_id = models.CharField(_("prior run id"), max_length=26, blank=True, default="")
    # The WatchActionDelivery (HTTP call) that carried this run, set when a
    # delivery action's call lands. Blank for non-delivery runs (filters), for
    # the local log action (no HTTP call), and before the call is made.
    delivery_id = models.CharField(_("delivery id"), max_length=26, blank=True, default="")

    class Meta:
        verbose_name = _("watch action run")
        verbose_name_plural = _("watch action runs")
        constraints = [
            # account_id-first for scoping + index coverage; the
            # (watch, action, feed_item) triple is already globally unique
            # (idempotency key for "this action already ran on this item").
            models.UniqueConstraint(
                fields=["account_id", "watch_id", "action_id", "feed_item_id"],
                name="uniq_watchactionrun_account_watch_action_item",
            ),
        ]
        indexes = [
            # The cron drain pulls due PENDING runs ordered by schedule;
            # DELIBERATELY account-agnostic ; the drain is a Global
            # cross-tenant scan, so no account_id prefix here.
            models.Index(fields=["state", "scheduled_at"], name="watchrun_state_sched_idx"),
            # Per-action audit log (magpie watch action runs <id>).
            models.Index(fields=["account_id", "action_id", "id"], name="watchrun_acct_action_idx"),
            # The digest flush gathers a digest action's PENDING runs ordered
            # least-tried-then-oldest (WatchActionRunService.digest_batch). This
            # covers the filter (account, action, state) AND the (attempts, id)
            # sort, so each capped slice is an index scan + LIMIT with no sort,
            # even for large multi-slice windows.
            models.Index(
                fields=["account_id", "action_id", "state", "attempts", "id"],
                name="watchrun_digest_gather_idx",
            ),
            # Activity-summary bucketing (`magpie activity summary`): per-(account,
            # action) GROUP BY state over a completed_at window. account+action
            # equality then a completed_at range = an index range scan instead
            # of scanning the action's whole run history as it grows. Plain
            # composite (standard SQL, any RDBMS) — no Postgres-only covering
            # payload ; reading state is a heap fetch and this is an interactive
            # query, not a drain-path one.
            models.Index(
                fields=["account_id", "action_id", "completed_at"],
                name="watchrun_activity_idx",
            ),
            # Telemetry heartbeat rollup (count_by_state_since): a cross-tenant
            # GROUP BY state over a completed_at window. DELIBERATELY account-
            # agnostic (a Global scan, like watchrun_state_sched_idx) -- the
            # account-first indexes above can't serve a bare completed_at range.
            models.Index(fields=["completed_at", "state"], name="watchrun_completed_state_idx"),
        ]

    def __str__(self) -> str:
        return f"run {self.action_id}:{self.feed_item_id} [{self.state}] ({self.id})"
