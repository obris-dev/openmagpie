"""WatchActionDeliveryService: account-scoped reads + writes for the outbound
HTTP-call audit (one WatchActionDelivery row per attempt).

`record` persists one attempt ; `list_for_action` backs the deliveries CLI /
API. There is NO server-side dedup: delivery is at-least-once (the POST is
outside the run's transaction) and receivers dedup per item on the in-body
`key`, so a replayed batch is collapsed receiver-side, not here.
"""

from __future__ import annotations

import builtins
from datetime import datetime

from django.utils import timezone

from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionDeliveryState, WatchActionRunState
from watches.actions.protocol import OutboundCall
from watches.models import WatchActionDelivery

# A run outcome maps to the delivery state of the call that produced it. Only
# the three terminal delivery outcomes occur (a config-invalid run makes no
# call, so it never reaches here) ; anything else is treated as ERRORED.
_OUTCOME_TO_DELIVERY = {
    WatchActionRunState.SUCCEEDED: WatchActionDeliveryState.SUCCEEDED,
    WatchActionRunState.ERRORED: WatchActionDeliveryState.ERRORED,
    WatchActionRunState.FAILED: WatchActionDeliveryState.FAILED,
}


class WatchActionDeliveryGlobal:
    """Static methods only. Span all accounts. Telemetry only."""

    @staticmethod
    def count_since(since: datetime) -> int:
        """Deliveries recorded since `since` across all accounts (24h rollup)."""
        return WatchActionDelivery.objects.filter(created_at__gte=since).count()


class WatchActionDeliveryService:
    """Account-scoped delivery-log surface."""

    Global = WatchActionDeliveryGlobal

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionDeliveryService requires account_id")
        self.account_id = account_id

    def record(
        self,
        *,
        watch_id: str,
        action_id: str,
        delivery: DeliveryCadence,
        call: OutboundCall,
        outcome_state: WatchActionRunState,
        error: str,
        attempt: int,
        now: datetime | None = None,
    ) -> WatchActionDelivery:
        """Persist one HTTP attempt as a terminal WatchActionDelivery row (the
        call already happened, so it lands terminal in one write)."""
        ts = now or timezone.now()
        state = _OUTCOME_TO_DELIVERY.get(outcome_state, WatchActionDeliveryState.ERRORED)
        return WatchActionDelivery.objects.create(
            account_id=self.account_id,
            watch_id=watch_id,
            action_id=action_id,
            delivery=delivery.value,
            method=call.method,
            target_host=call.target_host,
            state=state.value,
            http_status=call.http_status,
            item_count=call.item_count,
            attempt=attempt,
            request_payload=call.request_payload,
            error="" if state == WatchActionDeliveryState.SUCCEEDED else error,
            started_at=ts,
            completed_at=ts,
        )

    def get(self, delivery_id: str, /) -> WatchActionDelivery:
        """One delivery by its id (account-scoped). Raises
        WatchActionDelivery.DoesNotExist if missing / another account's."""
        return WatchActionDelivery.objects.get(id=delivery_id, account_id=self.account_id)

    def list_for_action(
        self,
        action_id: str,
        /,
        *,
        after: str | None = None,
        limit: int = 50,
        state: str | None = None,
    ) -> builtins.list[WatchActionDelivery]:
        """This account's deliveries for one action, newest-first (ULID pk).
        Cursor-paginated for the audit CLI ; `state` filters by delivery state."""
        qs = WatchActionDelivery.objects.filter(account_id=self.account_id, action_id=action_id)
        if state:
            qs = qs.filter(state=state)
        if after:
            qs = qs.filter(id__lt=after)
        return builtins.list(qs.order_by("-id")[:limit])
