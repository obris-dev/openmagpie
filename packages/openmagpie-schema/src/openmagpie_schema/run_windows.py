"""The run-list time-window filter contract (the `magpie activity export` flow).

The `[since, until)` query params that filter `GET /v1/actions/<id>/activity` by
the run's completion or the feed item's source time. The WIRE CONTRACT between
the CLI (`api/activity`) and the server (`ActionRunsView`): the names live here
ONCE so a rename can't silently desync the two processes. Kept out of `watch.py`
(the wire models) so neither file outgrows the length cap.

A window VALUE is a relative duration (`7d`, `24h`, `30m`, `2w`) or an absolute
ISO datetime; `resolve_window_value` / `resolve_run_windows` turn those into
absolute datetimes. Resolution lives here (shared, pure) and runs SERVER-side
against the server clock -- the one source of truth -- so the CLI forwards the raw
value rather than pre-resolving on its own (possibly skewed) clock, and a non-Python
client (a web UI) doesn't reimplement the grammar. `now` is passed in (zero-Django).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast

# `*_since`/`*_until` are `[since, until)` bounds; `completed_*` filters the run's
# completion, `occurred_*` the feed item's source time.
RUN_WINDOW_PARAMS = ("occurred_since", "occurred_until", "completed_since", "completed_until")


class RunWindows(TypedDict, total=False):
    """The resolved run-window bounds -- the keys ARE `list_for_action`'s kwarg
    names, so a caller spreads `**windows` instead of re-spelling them. total=False:
    only the set bounds are present."""

    occurred_since: datetime
    occurred_until: datetime
    completed_since: datetime
    completed_until: datetime


_REL_DURATION = re.compile(r"(\d+)([smhdw])")  # used with fullmatch, so no ^/$ anchors
_REL_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

# A lone `*_until` (no paired `*_since`) would scan unbounded-below; bound the
# `*_since` this far before the `*_until` instead.
RUN_WINDOW_DEFAULT_SPAN = timedelta(days=7)


def run_window_params(
    *,
    occurred_since: str | None = None,
    occurred_until: str | None = None,
    completed_since: str | None = None,
    completed_until: str | None = None,
) -> dict[str, str]:
    """Map the run-window values onto their `RUN_WINDOW_PARAMS` names, dropping the
    unset ones. The ONE place that pairs values to names (by name, not position) so
    the CLI query builder can't re-spell or mis-order them. Keyword-only so a caller
    can't transpose the four args."""
    by_name = {
        "occurred_since": occurred_since,
        "occurred_until": occurred_until,
        "completed_since": completed_since,
        "completed_until": completed_until,
    }
    # Derive from by_name itself (not by iterating RUN_WINDOW_PARAMS) so a name added
    # here can't be silently dropped by forgetting the tuple; a test pins the two in
    # sync. Insertion order == RUN_WINDOW_PARAMS order.
    return {name: value for name, value in by_name.items() if value}


def resolve_window_value(raw: str, *, now: datetime) -> datetime:
    """A `*_since`/`*_until` value -> an absolute aware datetime, anchored at `now`.

    Accepts a relative duration (`7d`, `24h`, `30m`, `2w`) measured back from `now`,
    or an absolute ISO datetime/date (a naive one is read as UTC). Raises `ValueError`
    on anything else (incl. a well-formed-but-out-of-range value like Feb 30); the
    caller maps that to its surface's error (a server 400, a CLI BadParameter).

    PRECONDITION: `now` is timezone-aware (callers pass `timezone.now()` /
    `datetime.now(UTC)`). A naive `now` yields naive relative results, which would
    then TypeError against the aware ISO ones in `resolve_run_windows`'s compare."""
    # Caller contract (an explicit raise, not an assert that python -O strips). A
    # TypeError, NOT ValueError -- a naive clock is a server bug, so it must NOT be
    # caught as a client 400 by the view's `except ValueError`.
    if now.tzinfo is None:
        raise TypeError("now must be timezone-aware")
    text = raw.strip()
    rel = _REL_DURATION.fullmatch(text)
    if rel:
        amount, unit = int(rel.group(1)), rel.group(2)
        try:
            return now - timedelta(**{_REL_UNIT[unit]: amount})
        except OverflowError:
            # The pattern allows unbounded digits; a huge duration overflows timedelta
            # (raises OverflowError, NOT ValueError) -> fold it into the ValueError path
            # so the caller still maps it to a 400 / BadParameter, never a 500.
            raise ValueError(f"{raw!r} is out of range") from None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{raw!r} is neither a duration (e.g. 7d, 24h) nor an ISO datetime") from None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def resolve_run_windows(raw: dict[str, str], *, now: datetime) -> RunWindows:
    """Resolve raw `RUN_WINDOW_PARAMS` values to absolute aware datetimes (relative
    durations anchored at `now`), applying the contract's two guards:
      - a lone `*_until` (no `*_since`) gets a `*_since` bound `RUN_WINDOW_DEFAULT_SPAN`
        before it, so it can't scan unbounded-below;
      - `since >= until` is the empty half-open window -> `ValueError`.
    Raises `ValueError` (naming the param) on any unresolvable value or inverted
    window; the caller maps it to a 400 / BadParameter."""
    resolved: dict[str, datetime] = {}
    for name, value in raw.items():
        try:
            resolved[name] = resolve_window_value(value, now=now)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from None
    for base in {param.rsplit("_", 1)[0] for param in RUN_WINDOW_PARAMS}:
        since, until = f"{base}_since", f"{base}_until"
        if until in resolved and since not in resolved:
            try:
                resolved[since] = resolved[until] - RUN_WINDOW_DEFAULT_SPAN
            except OverflowError:
                # An `*_until` so early that until - SPAN underflows datetime.min
                # (e.g. year 1). Fold to ValueError like the relative-duration path,
                # so it's a 400 not a 500 (this module's "never a 500" invariant).
                raise ValueError(f"{until} is too early to bound a window below") from None
        if since in resolved and until in resolved and resolved[since] >= resolved[until]:
            raise ValueError(f"{since} must be before {until}")
    # Built with dynamic str keys (RUN_WINDOW_PARAMS), so cast to the keyed shape.
    return cast("RunWindows", resolved)
