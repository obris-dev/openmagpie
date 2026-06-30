"""Telemetry API resource client.

Wraps `/v1/telemetry`: read the server's anonymous-telemetry state (any user)
and enable/disable it (account owner only). The client sends a consent INTENT;
the server resolves the concrete mode. `get()` backs the post-login disclosure
notice + `telemetry status`; `set_enabled()` backs `telemetry enable`/`disable`
(the decision lives server-side, see apps/core/telemetry).
"""

from __future__ import annotations

from openmagpie_schema.telemetry import TelemetryState

from .. import routes
from ..http import MagpieClient

__all__ = ["TelemetryApi", "TelemetryState"]


class TelemetryApi:
    """Resource client for `/v1/telemetry`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def get(self) -> TelemetryState:
        """GET /v1/telemetry -> the instance's mode + whether this user can set it."""
        return TelemetryState.model_validate(self._http.get(routes.telemetry.base))

    def set_enabled(self, *, enabled: bool) -> TelemetryState:
        """POST /v1/telemetry to enable/disable telemetry (account owner only). Sends
        the consent intent; the server resolves the mode (self-hosted: enable ->
        anonymous, disable -> off)."""
        return TelemetryState.model_validate(self._http.post(routes.telemetry.base, json_body={"enabled": enabled}))
