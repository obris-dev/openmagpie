"""Telemetry data model: a single settings singleton.

Flat `models.py` (not a `models/` package like the multi-model apps): telemetry is
a single-model singleton app, so the per-model split would be one file.
"""

from django.db import models

from common.models import BaseModel
from openmagpie_schema.telemetry import TelemetryMode

# TelemetryMode is the shared (CLI + server) source of truth; re-exported here so
# `from telemetry.models import TelemetryMode` keeps working for the app code.
__all__ = ["TelemetryMode", "TelemetrySettings"]


class TelemetrySettings(BaseModel):
    """Singleton row holding this instance's telemetry decision + anonymous id.

    One row per deployment, enforced at the DB level by the unique `singleton`
    flag (only one row can carry True). The decision lives server-side because
    the SERVER is what emits; interactive clients (quickstart, CLI) only read and
    set it. `instance_id` is a random UUID minted when the mode is first set to
    ANONYMOUS: it is the PostHog distinct_id, opaque and unlinked to any account,
    so the data is genuinely anonymous (not merely pseudonymous).
    """

    # Unique-True enforces "at most one row" at the database level.
    singleton = models.BooleanField(default=True, unique=True, editable=False)
    # No `choices=` on the column: the StrEnum is the source of truth and the
    # service validates writes (set_mode). Keeping choices off the field means a
    # new mode never forces a migration -- the house pattern (see the
    # WatchActionRun.state and Source.kind fields).
    mode = models.CharField(max_length=16, default=TelemetryMode.UNSET.value)
    instance_id = models.CharField(max_length=36, blank=True, default="")
    # When the heartbeat was last emitted. The heartbeat command self-throttles on
    # this (emit at most once per window) so it can ride the pipeline tickers --
    # which start/stop and run at varying cadences -- instead of assuming a steady
    # daily cron. Only advances while opted in.
    last_heartbeat_at = models.DateTimeField(null=True, blank=True, editable=False)

    # Mirror the wire model's is_* accessors so server-side gates read
    # `row.is_anonymous`, not `row.mode == TelemetryMode.ANONYMOUS.value`.
    @property
    def is_unset(self) -> bool:
        return self.mode == TelemetryMode.UNSET.value

    @property
    def is_off(self) -> bool:
        return self.mode == TelemetryMode.OFF.value

    @property
    def is_anonymous(self) -> bool:
        return self.mode == TelemetryMode.ANONYMOUS.value

    @property
    def is_identified(self) -> bool:
        return self.mode == TelemetryMode.IDENTIFIED.value

    @classmethod
    def current(cls) -> "TelemetrySettings":
        """The one settings row, created (mode=UNSET) on first access."""
        obj, _ = cls.objects.get_or_create(singleton=True)
        return obj
