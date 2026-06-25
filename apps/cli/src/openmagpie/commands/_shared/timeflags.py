"""Time-flag handling for export commands: the no-window default + client-side
validation.

The relative-duration / ISO grammar and the AUTHORITATIVE resolution live
server-side in `openmagpie_schema.run_windows` (resolved against the server clock,
the one source of truth). The CLI forwards the raw value and only validates it here
-- via the same shared `resolve_run_windows` -- for a fast, local error before the
round-trip; the value sent is still the raw string.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer

from openmagpie_schema.run_windows import resolve_run_windows, run_window_params

# With NO window flag, an export defaults to the last _DEFAULT_WINDOW on completion
# (export UX, NOT a server default -- the activity list endpoint has no default), so
# a no-arg export can't scan all retention. Sent as the raw relative value for the
# server to resolve; the label also drives the "defaulting to ..." announce.
_DEFAULT_WINDOW = "7d"


def _build_windows(
    *,
    occurred_since: str | None = None,
    occurred_until: str | None = None,
    completed_since: str | None = None,
    completed_until: str | None = None,
) -> tuple[dict[str, str], bool]:
    """Map the run-window flags to the RAW query values the server resolves (a
    duration like `7d` or an ISO datetime). The values are validated here (a fast
    BadParameter, via the shared resolver) but sent RAW -- the server owns the
    authoritative resolution (its clock), the until-without-since bound, and the
    ordering check. Returns `(windows, defaulted)`: with NO flag set, default to the
    last `_DEFAULT_WINDOW` on completion and flag it for the caller to announce."""
    raw = run_window_params(
        occurred_since=occurred_since,
        occurred_until=occurred_until,
        completed_since=completed_since,
        completed_until=completed_until,
    )
    try:
        resolve_run_windows(raw, now=datetime.now(UTC))  # validate (format + ordering); the raw values are sent
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if raw:
        return raw, False
    return {"completed_since": _DEFAULT_WINDOW}, True
