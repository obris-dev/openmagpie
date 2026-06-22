"""`manage.py emit_telemetry_heartbeat` -- one rolled-up telemetry event per ~day.

Rides the pipeline tickers (local-tick + an hourly up-jobs ticker): it
self-throttles on `TelemetrySettings.last_heartbeat_at`, emitting at most once per
`_MIN_INTERVAL` however often it's called, so it survives a self-hoster who
starts/stops the jobs stack rather than assuming a steady daily cron. Emits a
single `instance_heartbeat` with config gauges + a 24h rollup (one event/day, not
the thousands per-poll events would produce). A no-op unless the instance is in
ANONYMOUS mode; the capture path never raises into the cron.

All cross-tenant reads go through each owning service's `Global` aggregate (no
direct `Model.objects` here). There is no `engine_kind` -- the relevance engine is
any OpenAI-compatible `/v1` endpoint with no server-known backend kind, so we
report only `engine_reachable`.
"""

import platform
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.services import AccountService
from feeds.services import FeedService, SourceService
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.services import (
    WatchActionDeliveryService,
    WatchActionRunService,
    WatchActionService,
    WatchService,
)

from ... import client, events
from ...service import TelemetryService

_WINDOW = timedelta(hours=24)  # rollup lookback
_MIN_INTERVAL = timedelta(hours=20)  # min spacing between heartbeats (see handle)


class Command(BaseCommand):
    help = "Emit one rolled-up anonymous telemetry heartbeat, at most once per ~day (no-op unless opted in)."

    def handle(self, *args, **options):
        # Atomically claim the slot (opted in + due) with one conditional UPDATE, so
        # two overlapping pipeline ticks (local-tick / up-jobs) can't both pass a
        # read-then-act gap check and double-emit. The tickers start/stop and run at
        # varying cadences, so the heartbeat can't assume a steady daily cron -- it
        # rides whatever tick runs and self-throttles to once per _MIN_INTERVAL
        # (sub-24h, so a roughly-daily cadence never skips a day).
        now = timezone.now()
        if not TelemetryService.Global.claim_heartbeat(now=now, min_interval=_MIN_INTERVAL):
            return  # opted out, not due yet, or another tick already claimed it
        # Claiming stamped last_heartbeat_at up front; guard the gather+emit so a
        # stray aggregate error can't break the tick (a guarded failure misses this
        # window rather than double-sending).
        with events.guard():
            since = now - _WINDOW
            runs_by_state = WatchActionRunService.Global.count_by_state_since(since)
            props: events.HeartbeatProps = {
                # environment
                "os": platform.system(),
                "arch": platform.machine(),
                "engine_reachable": self._engine_reachable(),
                # config gauges (current totals). accounts: solo self-host vs multi-account.
                "accounts": AccountService.Global.count(),
                "feeds": FeedService.Global.count(),
                "watches": WatchService.Global.count(),
                "sources_by_kind": SourceService.Global.count_by_kind(),
                "actions_by_kind": WatchActionService.Global.count_by_kind(),
                # 24h rollups
                "runs_by_state": runs_by_state,
                "matches": runs_by_state.get(WatchActionRunState.SUCCEEDED.value, 0),
                "deliveries": WatchActionDeliveryService.Global.count_since(since),
            }
            events.heartbeat(props)
            client.flush()  # short-lived command: flush before the batch thread is torn down

    @staticmethod
    def _engine_reachable() -> bool:
        # engine.status() probes the configured LLM and never raises; guard the
        # import/iteration anyway so a heartbeat can't break on an engine quirk.
        try:
            from engine import registry

            return any(registry.get(kind).status().available for kind in registry.kinds())
        except Exception:
            return False
