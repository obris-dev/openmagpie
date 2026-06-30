"""Telemetry mode transitions + the heartbeat-stamp write, shared by the command
and the HTTP endpoint so both enforce the same rules: only `off`/`anonymous` are
settable (IDENTIFIED is hosted-only; UNSET is the initial opt-out-default state,
never a target), the anonymous `instance_id` is minted at row creation (telemetry
is opt-OUT), and the `telemetry_enabled` event fires exactly once on an explicit
switch into ANONYMOUS.

Flat `service.py` / `models.py` (not the `services/` + `models/` packages the
multi-model apps use): telemetry is a single-model singleton app, so the
per-resource split would be one file each.
"""

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
        `off`/`anonymous`. Fires `telemetry_enabled` once on a genuine re-enable
        (off -> anonymous). (instance_id is minted by the field default at row
        creation, so set_mode never touches it.)"""
        if mode not in SETTABLE_MODES:
            raise ValueError(f"telemetry mode must be one of {SETTABLE_MODES}, got {mode!r}")
        row = TelemetrySettings.current()
        was_off = row.is_off
        row.mode = mode
        row.save(update_fields=["mode", "updated_at"])
        # Fire telemetry_enabled only on a genuine re-enable (off -> anonymous): a real
        # off->on toggle. unset -> anonymous is not a re-enable (the opt-out default
        # already emits), so affirming the default fires nothing. Persisted above first,
        # so the capture() inside telemetry_enabled() sees mode == ANONYMOUS.
        if row.is_anonymous and was_off:  # row was just saved with `mode`, so this reads the new state
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
        `now` iff not opted out (mode != OFF, mirroring the `is_emitting` property,
        which this conditional UPDATE can't call) AND it's been >= min_interval (or
        never run). A single conditional UPDATE, so two overlapping pipeline ticks
        can't both pass a read-then-act gap check and
        double-emit. Returns True iff THIS call won the slot -- the caller should
        then aggregate + emit (the stamp is already persisted, so a later emit
        failure misses the window, not doubles).

        Assumes the singleton row exists: this is a pure conditional UPDATE (it can't
        create), and its one caller gates on `client.enabled()` first, which lazily
        creates the row via `current()`."""
        claimed = (
            TelemetrySettings.objects.filter(singleton=True)
            .exclude(mode=TelemetryMode.OFF.value)
            .filter(Q(last_heartbeat_at__isnull=True) | Q(last_heartbeat_at__lt=now - min_interval))
            .update(last_heartbeat_at=now)
        )
        return bool(claimed)


class TelemetryService:
    """The instance-wide telemetry decision. No account scoping (a single settings
    row), so every operation is system-level, under `Global`."""

    Global = TelemetryGlobal
