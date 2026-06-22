"""Telemetry mode transitions + the heartbeat-stamp write, shared by the command
and the HTTP endpoint so both enforce the same rules: only `off`/`anonymous` are
settable (IDENTIFIED is hosted-only; UNSET is the initial state, never a target),
the anonymous `instance_id` is minted on the first switch to ANONYMOUS, and the
`telemetry_enabled` opt-in event fires exactly once at opt-in.

Flat `service.py` / `models.py` (not the `services/` + `models/` packages the
multi-model apps use): telemetry is a single-model singleton app, so the
per-resource split would be one file each.
"""

import uuid
from datetime import datetime, timedelta

from django.db.models import Q

from .events import telemetry_enabled
from .models import TelemetryMode, TelemetrySettings

# Modes a self-hosted operator may set. IDENTIFIED is reserved for the hosted
# product; UNSET is the initial "not asked yet" state, not a settable target.
SETTABLE_MODES = (TelemetryMode.OFF.value, TelemetryMode.ANONYMOUS.value)


class TelemetryGlobal:
    """Static methods only. Instance-wide telemetry ops -- no account scoping (a
    single settings row) -- matching the `<Service>.Global` system-level pattern
    (mirrors AccountGlobal / FeedGlobal / the new SourceGlobal etc.)."""

    @staticmethod
    def set_mode(mode: str) -> TelemetrySettings:
        """Set the instance telemetry mode. Raises ValueError for anything but
        `off`/`anonymous`. Mints the anonymous `instance_id` on the first switch
        to ANONYMOUS and fires `telemetry_enabled` once, at opt-in."""
        if mode not in SETTABLE_MODES:
            raise ValueError(f"telemetry mode must be one of {SETTABLE_MODES}, got {mode!r}")
        row = TelemetrySettings.current()
        was_anonymous = row.is_anonymous
        row.mode = mode
        if mode == TelemetryMode.ANONYMOUS.value and not row.instance_id:
            row.instance_id = str(uuid.uuid4())
        row.save(update_fields=["mode", "instance_id", "updated_at"])
        # Fire the opt-in event on any transition INTO anonymous (from unset or
        # off) -- exactly when consent is (re)given -- but never on anon->anon
        # (idempotent) nor when setting off. Persisted above first, so the
        # capture() inside telemetry_enabled() sees mode == ANONYMOUS.
        if mode == TelemetryMode.ANONYMOUS.value and not was_anonymous:
            telemetry_enabled()
        return row

    @staticmethod
    def set_enabled(*, enabled: bool) -> TelemetrySettings:
        """Apply a consent INTENT, resolving it to the concrete mode for this
        deployment: self-hosted maps enable -> ANONYMOUS, disable -> OFF. (The
        hosted build will resolve enable -> IDENTIFIED; that is the deferred seam.)
        The CLI + endpoint speak this intent so a client never hardcodes a mode."""
        mode = TelemetryMode.ANONYMOUS.value if enabled else TelemetryMode.OFF.value
        return TelemetryGlobal.set_mode(mode)

    @staticmethod
    def claim_heartbeat(*, now: datetime, min_interval: timedelta) -> bool:
        """Atomically claim the daily heartbeat slot: stamp last_heartbeat_at to
        `now` iff opted in (ANONYMOUS) AND it's been >= min_interval (or never run).
        A single conditional UPDATE, so two overlapping pipeline ticks can't both
        pass a read-then-act gap check and double-emit. Returns True iff THIS call
        won the slot -- the caller should then aggregate + emit (the stamp is
        already persisted, so a later emit failure misses the window, not doubles)."""
        claimed = (
            TelemetrySettings.objects.filter(singleton=True, mode=TelemetryMode.ANONYMOUS.value)
            .filter(Q(last_heartbeat_at__isnull=True) | Q(last_heartbeat_at__lt=now - min_interval))
            .update(last_heartbeat_at=now)
        )
        return bool(claimed)


class TelemetryService:
    """The instance-wide telemetry decision. No account scoping (a single settings
    row), so every operation is system-level, under `Global`."""

    Global = TelemetryGlobal
