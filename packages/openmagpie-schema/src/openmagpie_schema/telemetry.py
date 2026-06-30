"""Telemetry mode enum + API wire shapes.

Shared, zero-Django source of truth for the telemetry mode and the
`/v1/telemetry` responses. Both the server (its `TelemetrySettings` model) and
the CLI compare against `TelemetryMode`, so the values live here and neither side
hard-codes the strings. `TelemetryState.mode` stays a plain `str` on the wire
(forward-compatible: an older CLI talking to a newer server with an unknown mode
just doesn't match `UNSET` and skips the notice, rather than failing to parse).
"""

from enum import StrEnum

from pydantic import BaseModel


class TelemetryMode(StrEnum):
    """How an instance reports product telemetry.

    - UNSET: the default, never explicitly chosen. EMITS anonymous telemetry
      (opt-OUT) and signals that an interactive entry point (quickstart, first
      admin CLI login) should show the one-time disclosure notice. Distinct from
      OFF so "default / not yet decided" is not confused with "explicitly declined".
    - OFF: explicitly opted out. Emits nothing, never re-notified.
    - ANONYMOUS: explicitly kept on. Same anonymous emission as UNSET, keyed by a
      random instance_id (no account, no PII). Self-hosted emits in UNSET (default)
      and ANONYMOUS; only OFF is silent.
    - IDENTIFIED: account-keyed telemetry, reserved for the future hosted product
      (built with it, against the real account model). Refused on self-hosted.
    """

    UNSET = "unset"
    OFF = "off"
    ANONYMOUS = "anonymous"
    IDENTIFIED = "identified"


class TelemetryState(BaseModel):
    """The instance's telemetry mode, whether this user may change it (`can_set`), and
    the server-computed `emitting` (will an event actually send right now; see the field).

    The `is_*` helpers keep callers off the raw `mode` string -- the enum
    comparison happens once, here, so a consumer reads `state.is_unset` rather
    than `state.mode == "unset"`.
    """

    mode: str
    can_set: bool
    # Server-computed "will an event actually send right now": the same gate capture()
    # uses (opt-out mode AND DO_NOT_TRACK unset AND a configured key). All three are
    # server-side, so a client can't derive it; `None` only when talking to a server too
    # old to report it. Lets the CLI status show that e.g. `unset` means on-and-sending.
    emitting: bool | None = None

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
