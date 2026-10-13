"""Time-flag handling for export commands: the no-window default + client-side
validation.

The keyword / relative-duration / ISO grammar and the AUTHORITATIVE resolution
live server-side in `openmagpie_schema.run_windows` (resolved against the server clock,
the one source of truth). The CLI forwards the raw value and only validates it here
-- via the same shared `resolve_run_windows` -- for a fast, local error before the
round-trip; the value sent is still the raw string.
"""

from __future__ import annotations

from datetime import UTC, datetime

import typer

from openmagpie_schema.run_windows import keyword_choices, resolve_run_windows, run_window_params

# With NO window flag, an export defaults to the last _DEFAULT_WINDOW on completion
# (export UX, NOT a server default -- the activity list endpoint has no default), so
# a no-arg export can't scan all retention. Sent as the raw relative value for the
# server to resolve; the label also drives the "defaulting to ..." announce.
_DEFAULT_WINDOW = "7d"


# The ONE description of a window value for the since/until flag help, derived
# from the enum so the keyword list can't drift. `now` is the server clock; the
# day keywords are UTC-midnight INSTANTS, not spans, which is what WINDOW_DAY_HINT
# (appended on the `*-since` flags only) spells out.
def window_value_grammar(*, with_floor: bool) -> str:
    """The grammar sentence for a flag's help. `with_floor=True` (a seek target)
    lists `oldest`; the run-window filters have no floor, so it is left out."""
    return (
        f"a keyword ({keyword_choices(with_floor=with_floor)}; `now` is the server clock, the day keywords are "
        "UTC midnights), a duration back from now (`7d` = a week ago, `2h` = two hours ago), or an ISO datetime"
    )


WINDOW_VALUE_GRAMMAR = window_value_grammar(with_floor=False)
WINDOW_DAY_HINT = "A keyword is an instant, so a whole day is `--<axis>-since yesterday --<axis>-until today`"


def validated_window_params(
    *,
    occurred_since: str | None = None,
    occurred_until: str | None = None,
    completed_since: str | None = None,
    completed_until: str | None = None,
) -> dict[str, str]:
    """Map the run-window flags to the RAW query values the server resolves (a
    keyword like `yesterday`, a duration like `7d`, or an ISO datetime) and validate
    them (format + ordering) via
    the shared resolver, raising `typer.BadParameter` on a bad value -- a fast, local
    error before the round-trip. The values are still sent RAW: the server owns the
    authoritative resolution (its clock) + the until-without-since bound. The EMPTY
    result (no flag set) is returned as-is; each caller decides what that means (an
    export defaults it, a backfill requires it)."""
    raw = run_window_params(
        occurred_since=occurred_since,
        occurred_until=occurred_until,
        completed_since=completed_since,
        completed_until=completed_until,
    )
    try:
        resolve_run_windows(raw, now=datetime.now(UTC))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    return raw


def _build_windows(
    *,
    occurred_since: str | None = None,
    occurred_until: str | None = None,
    completed_since: str | None = None,
    completed_until: str | None = None,
) -> tuple[dict[str, str], bool]:
    """The export's window resolution: `validated_window_params` + the no-window
    default. Returns `(windows, defaulted)`: with NO flag set, default to the last
    `_DEFAULT_WINDOW` on completion and flag it for the caller to announce."""
    raw = validated_window_params(
        occurred_since=occurred_since,
        occurred_until=occurred_until,
        completed_since=completed_since,
        completed_until=completed_until,
    )
    if raw:
        return raw, False
    return {"completed_since": _DEFAULT_WINDOW}, True
