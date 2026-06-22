"""Typed, non-PII event helpers.

The ONLY place event names and property shapes are defined, so call sites stay
clean and no caller can accidentally attach content. Properties are enums /
counts / version / surface ONLY -- never queries, instructions, URLs, titles,
or match text. Every helper routes through `client.capture`, which gates on
mode and never raises.
"""

import contextlib
import logging
from collections.abc import Iterable
from typing import TypedDict

from . import client

logger = logging.getLogger("telemetry")


class HeartbeatProps(TypedDict):
    """The instance_heartbeat property schema -- environment + config gauges + 24h
    rollups, all non-PII counts/strings. A TypedDict (not a bare dict) so the one
    event with an assembled payload still can't quietly grow arbitrary keys, keeping
    the "no content" guarantee uniform with the keyword-typed milestone helpers."""

    os: str
    arch: str
    engine_reachable: bool
    accounts: int
    feeds: int
    watches: int
    sources_by_kind: dict[str, int]
    actions_by_kind: dict[str, int]
    runs_by_state: dict[str, int]
    matches: int
    deliveries: int


@contextlib.contextmanager
def guard():
    """Swallow + log any exception from a best-effort telemetry block, so a
    telemetry hiccup never disturbs the operation it hangs off. It covers the
    call-site prop gathering (a stray query, a bad comprehension); `capture`
    already swallows + logs emit failures itself, with the event name. No label:
    the logged traceback already pinpoints the call site. Use as:

        with telemetry_events.guard():
            ...gather props...
            telemetry_events.feed_created(...)
    """
    try:
        yield
    except Exception:
        logger.exception("telemetry block failed")


def enabled() -> bool:
    """See `client.enabled` -- exposed here so a hot call site that already imports
    this module can cheaply gate before gathering props (e.g. skip a query)."""
    return client.enabled()


def telemetry_enabled() -> None:
    """The opt-in moment (consent just given) -- the clean numerator event."""
    client.capture("telemetry_enabled")


def feed_created(*, source_count: int, connector_kinds: Iterable[str], surface: str) -> None:
    client.capture(
        "feed_created",
        {"source_count": source_count, "connector_kinds": sorted(set(connector_kinds)), "surface": surface},
    )


def watch_created(*, action_kinds: Iterable[str], feed_count: int, surface: str) -> None:
    client.capture(
        "watch_created",
        {"action_kinds": sorted(set(action_kinds)), "feed_count": feed_count, "surface": surface},
    )


def first_match(*, action_kind: str, surface: str) -> None:
    """A watch's first-ever match: the activation / time-to-first-value signal."""
    client.capture("first_match", {"action_kind": action_kind, "surface": surface})


def quickstart_completed(*, surface: str) -> None:
    """Quickstart finished successfully (emitted post-consent, so it only sends
    if the operator opted in). Pairs with the consent-free /install.sh hit count
    for the quickstart funnel (started -> completed)."""
    client.capture("quickstart_completed", {"surface": surface})


def heartbeat(properties: HeartbeatProps) -> None:
    """One rolled-up event per instance per day (gauges + since-last counts).
    The caller (the heartbeat command) assembles the non-PII property dict; the
    HeartbeatProps TypedDict pins its shape."""
    client.capture("instance_heartbeat", dict(properties))
