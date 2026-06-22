"""Telemetry mode enum + API wire shapes.

Shared, zero-Django source of truth for the telemetry mode and the
`/v1/telemetry` responses. Both the server (its `TelemetrySettings` model) and
the CLI compare against `TelemetryMode`, so the values live here and neither side
hard-codes the strings. `TelemetryState.mode` stays a plain `str` on the wire
(forward-compatible: an older CLI talking to a newer server with an unknown mode
just doesn't match `UNSET` and skips the prompt, rather than failing to parse).
"""

from enum import StrEnum

from pydantic import BaseModel


class TelemetryMode(StrEnum):
    """How an instance reports product telemetry.

    - UNSET: never asked. Emits NOTHING (so opt-in holds) but signals that an
      interactive entry point (quickstart, first admin CLI login) should prompt.
      Distinct from OFF so "not asked yet" is not confused with "declined".
    - OFF: explicitly opted out. Emits nothing, never re-prompted.
    - ANONYMOUS: opted in to anonymous telemetry, keyed by a random instance_id
      (no account, no PII). The only "on" mode self-hosted uses.
    - IDENTIFIED: account-keyed telemetry, reserved for the future hosted product
      (built with it, against the real account model). Refused on self-hosted.
    """

    UNSET = "unset"
    OFF = "off"
    ANONYMOUS = "anonymous"
    IDENTIFIED = "identified"


class TelemetryState(BaseModel):
    """The instance's telemetry mode + whether this user may change it.

    The `is_*` helpers keep callers off the raw `mode` string -- the enum
    comparison happens once, here, so a consumer reads `state.is_unset` rather
    than `state.mode == "unset"`.
    """

    mode: str
    can_set: bool

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


class Surface(StrEnum):
    """Which client/context drove an event, attached to milestone events. The
    client declares it via SURFACE_HEADER and the server allowlists the inbound
    members; SYSTEM is server-set only (the scheduler / quickstart) and is never
    accepted from the header. Shared so the CLI and server never drift.
    """

    CLI = "cli"
    WEB = "web"
    API = "api"
    SYSTEM = "system"


# The header a client sets to declare its surface -- a header NAME, not a Surface
# vocabulary member, so it stays a bare constant.
SURFACE_HEADER = "X-Magpie-Surface"
