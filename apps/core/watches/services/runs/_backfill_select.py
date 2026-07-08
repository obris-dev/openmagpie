"""BackfillSelectMixin: the run-table reads + terminal-delete backing the
`magpie backfill` flow (source selection + `--replace` cleanup).

Split out of `WatchActionRunService` (`_service.py`) so neither file outgrows the
350-line cap; the mixin is account-scoped like its host (it reads `self.account_id`
off the service). `_apply_run_windows` lives here because both `list_for_action`
(the activity/export read) and the backfill source selectors share the two-window
filter, so the occurred-subquery logic stays in one place.
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Q, Subquery

from feeds.services import FeedItemService
from watches.models import WatchActionRun

from ._common import _FAILED, _SUCCEEDED, _TERMINAL

# Deletable-terminal: a clean terminal state, OR an EXHAUSTED failure (FAILED with a
# completed_at stamped, meaning the attempts cap was hit; completion_ts's rule). A
# RETRYING failure (FAILED, completed_at NULL) is still the drain's, so it's excluded.
# Shared by the delete (replace) and the dry-run's would_delete count so they agree.
_DELETABLE_TERMINAL = Q(state__in=_TERMINAL) | Q(state=_FAILED, completed_at__isnull=False)


class BackfillSelectMixin:
    """Run-table selection + terminal-delete for the backfill. Mixed into
    `WatchActionRunService`, which provides `self.account_id`."""

    account_id: str

    def _apply_run_windows(
        self,
        qs,
        /,
        *,
        completed_since: datetime | None = None,
        completed_until: datetime | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
    ):
        """Apply the two independent `[since, until)` run windows to a run queryset:
        `completed_*` on the run's `completed_at` (indexed residual), `occurred_*` on
        the FEED ITEM's source time via a `feed_item_id` subquery owned by
        `FeedItemService` (feed_item_id is a plain CharField, no ORM relation). NULL
        `occurred_at` fails the bound -> excluded. Shared by `list_for_action` and the
        backfill selectors so the occurred-subquery logic lives in one place."""
        if completed_since is not None:
            qs = qs.filter(completed_at__gte=completed_since)
        if completed_until is not None:
            qs = qs.filter(completed_at__lt=completed_until)
        if occurred_since is not None or occurred_until is not None:
            item_id_subquery = FeedItemService(account_id=self.account_id).occurred_window_id_subquery(
                since=occurred_since, until=occurred_until
            )
            qs = qs.filter(feed_item_id__in=item_id_subquery)
        return qs

    def _succeeded_in_window(
        self,
        source_action_id: str,
        /,
        *,
        completed_since: datetime | None = None,
        completed_until: datetime | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
    ):
        """Private run queryset for the two public consumers below: this account's
        SUCCEEDED runs of `source_action_id` within the windows. Kept private so
        callers get a definitive count / `Subquery`, not a malleable QuerySet."""
        qs = WatchActionRun.objects.filter(account_id=self.account_id, action_id=source_action_id, state=_SUCCEEDED)
        return self._apply_run_windows(
            qs,
            completed_since=completed_since,
            completed_until=completed_until,
            occurred_since=occurred_since,
            occurred_until=occurred_until,
        )

    def count_succeeded_feed_items(
        self,
        source_action_id: str,
        /,
        *,
        completed_since: datetime | None = None,
        completed_until: datetime | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
    ) -> int:
        """How many source passes fall in the window (the backfill's `matched`)."""
        return self._succeeded_in_window(
            source_action_id,
            completed_since=completed_since,
            completed_until=completed_until,
            occurred_since=occurred_since,
            occurred_until=occurred_until,
        ).count()

    def succeeded_feed_item_ids_subquery(
        self,
        source_action_id: str,
        /,
        *,
        completed_since: datetime | None = None,
        completed_until: datetime | None = None,
        occurred_since: datetime | None = None,
        occurred_until: datetime | None = None,
    ) -> Subquery:
        """The source passes' `feed_item_id`s as a `Subquery`, to intersect with
        feed-item existence via `existing_feed_item_ids_subquery` (`id__in=<this>`,
        server-side; the potentially-large set never materializes). A `Subquery`
        expression, NOT a QuerySet, so the caller can only use it as an `__in` operand."""
        return Subquery(
            self._succeeded_in_window(
                source_action_id,
                completed_since=completed_since,
                completed_until=completed_until,
                occurred_since=occurred_since,
                occurred_until=occurred_until,
            ).values("feed_item_id")
        )

    def delete_terminal_for_action(self, action_id: str, /, *, watch_id: str, feed_item_subquery: Subquery) -> int:
        """Delete this account's TERMINAL runs of `action_id` whose feed_item_id is in
        `feed_item_subquery` (the `present` `Subquery`, NOT a materialized list): a
        `replace` backfill clearing stale output, reused per downstream action. One DB
        DELETE: the subquery keeps it server-side (no bind-param ceiling, no Python
        ids). Only terminal rows go; a PENDING/RUNNING run is in-flight work the drain
        owns, so leaving it avoids racing the completion CAS (the re-enqueue is
        idempotent against it). Returns the rows deleted."""
        count, _ = WatchActionRun.objects.filter(
            _DELETABLE_TERMINAL,
            account_id=self.account_id,
            watch_id=watch_id,
            action_id=action_id,
            feed_item_id__in=feed_item_subquery,
        ).delete()
        return count

    def count_runs_for_action(
        self, action_id: str, /, *, watch_id: str, feed_item_subquery: Subquery, terminal_only: bool = False
    ) -> int:
        """Count this account's runs of `action_id` whose feed_item_id is in
        `feed_item_subquery` (the `present` `Subquery`). `terminal_only` restricts to
        the deletable-terminal set (what `delete_terminal_for_action` removes), backing
        the dry-run's `would_delete`; the unrestricted count backs `would_enqueue`. One
        COUNT, read-only (the preview)."""
        qs = WatchActionRun.objects.filter(
            account_id=self.account_id, watch_id=watch_id, action_id=action_id, feed_item_id__in=feed_item_subquery
        )
        if terminal_only:
            qs = qs.filter(_DELETABLE_TERMINAL)
        return qs.count()
