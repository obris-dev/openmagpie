"""DRF input serializer for the telemetry endpoint.

The output is a Pydantic `TelemetryState` (shared with the CLI); only the input
goes through a DRF serializer, per the repo's "serializer for input" rule.
"""

from rest_framework import serializers


class TelemetryConsentInput(serializers.Serializer):
    """POST /v1/telemetry body: a consent intent (`{"enabled": <bool>}`). The
    server resolves it to the concrete mode for this deployment (self-hosted:
    enable -> anonymous, disable -> off), so a client never names a mode and
    `identified` stays a server-internal, hosted-only concern. A missing/non-bool
    `enabled` is a 400, not a 500 from the service."""

    enabled = serializers.BooleanField()
