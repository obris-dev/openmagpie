"""HTTP entry points for backfills (the `magpie backfill` flow).

`BackfillActionView` is `POST /v1/actions/<action_id>/backfill`: it validates +
resolves the source/window, and either returns a synchronous read-only
`BackfillPreview` (`?dry_run=true`) or QUEUES a `WatchActionBackfill` job and
returns it (the fast path; the heavy select/delete/enqueue is `process_due_backfills`,
off the request). `BackfillListView` / `BackfillDetailView` read jobs back
(`GET /v1/action-backfills[/<id>]`).
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.api_params import parse_limit, wants_dry_run
from openmagpie_schema.backfill import BackfillListResponse, BackfillPreview
from openmagpie_schema.run_windows import RUN_WINDOW_PARAMS, resolve_run_windows

from .api import ActionScopedAPIView, WatchActionBackfillNotFound, WatchSvcMixin
from .models import WatchAction, WatchActionBackfill
from .operations.backfill import chain_from, resolve_present
from .serializers_backfill import BackfillInputSerializer, backfill_job_wire


class BackfillActionView(ActionScopedAPIView):
    """POST /v1/actions/<action_id>/backfill — queue a backfill of this action (or
    `?dry_run=true` for a synchronous size preview)."""

    def post(self, request, action_id: str):
        target = self.action  # 404 if absent from this account
        # BackfillInputSerializer coerces `replace` through a DRF BooleanField (a JSON
        # bool, a form-encoded string, or a JSON string all resolve; garbage 400s), so
        # bool("false")-style truthiness can't trigger the destructive delete.
        inp = BackfillInputSerializer(data=request.data if isinstance(request.data, dict) else {})
        inp.is_valid(raise_exception=True)
        replace = inp.validated_data["replace"]

        # Window: REQUIRED (no default), resolved server-side (server clock). The two
        # axes AND-combine (occurred_* on the item's source time, completed_* on the
        # source run's completion); a chain-head target has no upstream run, so
        # completed_* is rejected below with a 400. Values stay raw (7d / ISO).
        raw = {name: value for name in RUN_WINDOW_PARAMS if (value := inp.validated_data.get(name))}
        if not raw:
            return Response(
                {
                    "windows": [
                        "a time window is required; pass occurred_since/occurred_until or completed_since/completed_until"
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            windows = resolve_run_windows(raw, now=timezone.now())
        except ValueError as exc:
            return Response({"windows": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        prev = self.action_svc.prev_in_chain(target)
        source_is_head = prev is None
        source_action_id = "" if source_is_head else str(prev.id)
        # A chain-head target has no upstream RUN, so a completion window is meaningless.
        if source_is_head and ("completed_since" in windows or "completed_until" in windows):
            return Response(
                {"windows": ["a chain-head action has no upstream run; use occurred_since/occurred_until"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        watch_id = self.watch_svc.watch_id_for_path(str(target.path_id))

        if wants_dry_run(request):
            preview = self._preview(
                target=target,
                source_action_id=source_action_id,
                source_is_head=source_is_head,
                watch_id=watch_id,
                windows=windows,
                replace=replace,
            )
            return Response(preview.model_dump(mode="json"), status=status.HTTP_200_OK)

        job = self.backfill_svc.create(
            watch_id=watch_id,
            target_action_id=str(target.id),
            source_action_id=source_action_id,
            source_is_head=source_is_head,
            kind=str(target.kind),
            replace=replace,
            windows=windows,
            scheduled_at=timezone.now(),
        )
        return Response(backfill_job_wire(job).model_dump(mode="json"), status=status.HTTP_201_CREATED)

    def _preview(
        self, *, target: WatchAction, source_action_id: str, source_is_head: bool, watch_id: str, windows, replace: bool
    ) -> BackfillPreview:
        """Synchronous, READ-ONLY size preview (no job, no writes). All COUNTs over the
        shared `present` subquery, nothing materialized. `would_delete` mirrors the
        eventual `deleted`: target + downstream terminal runs (replace only)."""
        present = resolve_present(
            account_id=self.request.account_id,
            source_action_id=source_action_id,
            source_is_head=source_is_head,
            watch_id=watch_id,
            windows=windows,
        )  # .id_stream is unused here (lazy iterator), so the preview stays read-only counts
        existing_count = self.run_svc.count_runs_for_action(
            str(target.id), watch_id=watch_id, feed_item_subquery=present.subquery
        )
        would_delete = would_enqueue = 0
        if replace:
            # would_delete sums the target + every downstream action (the same
            # chain_from walk the executor deletes over, so they can't diverge).
            would_delete = sum(
                self.run_svc.count_runs_for_action(
                    str(action.id), watch_id=watch_id, feed_item_subquery=present.subquery, terminal_only=True
                )
                for action in chain_from(self.action_svc, target)
            )
            # The enqueue math uses the TARGET's terminal count (only the target is
            # enqueued): after deleting those, items still carrying a (non-terminal)
            # target run are skipped by the idempotent enqueue.
            delete_target_count = self.run_svc.count_runs_for_action(
                str(target.id), watch_id=watch_id, feed_item_subquery=present.subquery, terminal_only=True
            )
            would_enqueue = present.present_count - existing_count + delete_target_count
        else:
            would_enqueue = present.present_count - existing_count  # additive: only never-processed items
        return BackfillPreview(
            dry_run=True,
            source_action_id=source_action_id,
            source_is_head=source_is_head,
            replace=replace,
            matched=present.matched,
            present=present.present_count,
            pruned=present.matched - present.present_count,
            would_delete=would_delete,
            would_enqueue=would_enqueue,
        )


class BackfillListView(WatchSvcMixin, AccountScopedAPIView):
    """GET /v1/action-backfills — this account's backfill jobs, newest-first, cursor-
    paginated (`?after=<id>`, `?limit=`)."""

    def get(self, request):
        after = request.query_params.get("after") or None
        limit = parse_limit(request)
        jobs = self.backfill_svc.list(after=after, limit=limit)
        next_cursor = str(jobs[-1].id) if len(jobs) == limit else None
        return Response(
            BackfillListResponse(items=[backfill_job_wire(job) for job in jobs], next_cursor=next_cursor).model_dump(
                mode="json"
            )
        )


class BackfillDetailView(WatchSvcMixin, AccountScopedAPIView):
    """GET /v1/action-backfills/<backfill_id> — one backfill job's state + progress."""

    def get(self, request, backfill_id: str):
        try:
            job = self.backfill_svc.get(backfill_id)
        except WatchActionBackfill.DoesNotExist as exc:
            raise WatchActionBackfillNotFound(backfill_id) from exc
        return Response(backfill_job_wire(job).model_dump(mode="json"))
