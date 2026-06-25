"""Watch-domain discriminator enums (shared, zero-Django).

The Python-side source of truth for the watch `kind` / run `state` /
delivery cadence values. They live HERE (not server-only) so both the
server AND the magpie CLI type against the same enum instead of branching
on magic strings ("no state magic strings" convention). The server's
`watches.constants` re-exports these so `watches.constants.X` keeps
working ; the DB columns stay bare CharFields (no `choices=`), so adding /
removing a value never forces a migration.
"""

from enum import StrEnum


def choices(enum: type[StrEnum]) -> str:
    """Pipe-joined enum values for help text / error messages, derived from
    the enum so a hand-listed copy can't drift ("no state magic strings")."""
    return " | ".join(e.value for e in enum)


class WatchActionKind(StrEnum):
    """The kind of node in a watch's action chain ; selects the impl + the
    config/result contract.

    Three families:
      - FILTER:   semantic_filter ; gates the chain (a pass=false GATES).
      - EXTRACT:  extract ; hydrates declared fields onto the run's result.
                  Gates nothing, delivers nothing ; always advances.
      - DELIVERY: webhook, log ; emit the item outward. Delivery cadence
                  (instant vs digest) is a `delivery` field in the action's
                  config, NOT a separate kind.
    """

    SEMANTIC_FILTER = "semantic_filter"
    EXTRACT = "extract"
    WEBHOOK = "webhook"
    LOG = "log"


class DeliveryCadence(StrEnum):
    """Cadence of a DELIVERY action (webhook / log): emit per item, or
    batch a window into one emission. A field in the action's config, not
    a separate kind."""

    INSTANT = "instant"
    DIGEST = "digest"


class WebhookMethod(StrEnum):
    """HTTP verb a webhook action delivers with. Body-bearing verbs only:
    GET (no body) is deferred (it would map item fields to query params, a
    separate shape). PUT / PATCH are idempotent, which pairs with the
    delivery dedup."""

    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"


class WatchActionDeliveryState(StrEnum):
    """Lifecycle of one WatchActionDelivery (one outbound HTTP call attempt).

    PENDING is set when the row is created, just before the call; the
    response moves it terminal. Mirrors the delivery half of
    `WatchActionRunState`:
      - SUCCEEDED : a 2xx response.
      - ERRORED   : a PERMANENT failure (blocked destination, redirect, a
                    non-retryable 4xx) ; the call won't be retried.
      - FAILED    : a TRANSIENT failure (5xx / 408 / 429 / connect / timeout).
                    The owning run stays retryable, so a later attempt makes a
                    NEW delivery row.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    ERRORED = "errored"
    FAILED = "failed"


class WatchActivityWindow(StrEnum):
    """Bounded time windows for the action-activity summary, selected by a
    client and resolved to concrete `(since, until)` bounds SERVER-side (one
    source of truth, server clock). Applied by run EVALUATION time
    (`completed_at`). No unbounded 'all' value: a count is always over a
    finite range. Default is `WEEK`.
    """

    DAY = "24h"
    YESTERDAY = "yesterday"
    WEEK = "7d"
    MONTH = "30d"


class WatchActionRunState(StrEnum):
    """Lifecycle of one WatchActionRun (one action executing one item).

    The runner advances the chain to the next action IFF a run reaches
    `SUCCEEDED`. The control-flow fact lives in this column so the audit
    log is self-documenting (the score etc. stay in `result`).

    Terminal states, and how the drain treats each:
      - SUCCEEDED : ran, score met threshold -> advance the chain.
      - GATED     : ran cleanly, score below threshold -> chain stops. Not
                    a failure ; the expected "didn't pass the filter" path.
      - FAILED    : a TRANSIENT error (engine down, timeout, bad response).
                    Retryable -> the drain re-claims it until attempts hit
                    WATCH_RUN_MAX_ATTEMPTS, then it stays FAILED.
      - ERRORED   : a PERMANENT backend defect (e.g. a feed item whose
                    stored data can't be rehydrated). Terminal, NEVER
                    retried ; distinct from FAILED so an audit query can
                    tell "broken, look at it" from "transient, gave up".
      - SKIPPED   : a DELIBERATE non-run (operator paused the watch, a
                    policy chose not to run this action). Reserved for that
                    intent ; NOT used for defects (use ERRORED).
    Non-terminal: PENDING (queued) -> RUNNING (claimed by the drain).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    GATED = "gated"
    FAILED = "failed"
    ERRORED = "errored"
    SKIPPED = "skipped"


# State classification — the SINGLE source of truth, referenced instead of
# re-listing states across the drain, the services, and the CLI summary.
#
#   BACKLOG    : not run to a resting state yet (the live queue).
#   CLAIMABLE  : the drain may pick it up — PENDING (never run) or FAILED
#                (transient, retryable while under the attempts cap).
#   TERMINAL   : reached a final resting state ; never re-claimed. NOTE FAILED
#                is NOT here: it's terminal ONLY once attempts are exhausted,
#                which needs the attempts count, so terminality of a FAILED
#                run is decided alongside the cap (see the server's
#                `completion_ts`), not by membership in this set.
#
# A retryable FAILED (transient, under the cap) is therefore in NEITHER
# BACKLOG_STATES nor TERMINAL_STATES — it's classified positionally by
# completion_ts (no completed_at => "retrying"). So BACKLOG_STATES is the
# pending+running queue only and deliberately UNDERCOUNTS the live work ; a
# caller wanting the full live queue adds the retry-pending failures
# (FAILED with no completed_at) — what the summary's `retrying` bucket is.
BACKLOG_STATES = frozenset({WatchActionRunState.PENDING, WatchActionRunState.RUNNING})
CLAIMABLE_STATES = frozenset({WatchActionRunState.PENDING, WatchActionRunState.FAILED})
TERMINAL_STATES = frozenset(
    {
        WatchActionRunState.SUCCEEDED,
        WatchActionRunState.GATED,
        WatchActionRunState.ERRORED,
        WatchActionRunState.SKIPPED,
    }
)
