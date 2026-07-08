"""Operator-facing `WatchActionRun.error` messages.

A run's `error` is surfaced in the runs audit (CLI / API), so it is
EXTERNAL-facing text: a plain-language explanation of what happened, never
a raw exception. No `type(exc).__name__`, no library internals, no payload
contents ; those leak implementation detail and can carry sensitive data.
The raw cause is written to the server logs (keyed by run id) at the point
of failure, so an operator who needs the exact error can still find it ;
this module is just the human-readable surface.

One home for every such string so the no-leak policy is reviewable in a
single place instead of drifting across the drain, the reaper, and each
action impl.
"""

# Permanent defects (run state ERRORED) ; retrying cannot help.
ACTION_GONE = "This action was removed before the item could be processed."
ITEM_GONE = "The source item is no longer available (it may have aged out of retention)."
NO_EXECUTOR = "This action type can't be run by the current server."
ITEM_UNREADABLE = "The source item couldn't be read (its stored format is no longer recognized)."
CONFIG_INVALID = "This action's configuration is invalid and can't be run."
ENGINE_UNAVAILABLE = "The configured engine isn't available on this server."
# The LLM answered, but rejected the request as malformed/unauthorized/not-found
# (a 4xx retrying can't fix) - a server-config defect, not a transient blip.
ENGINE_REJECTED = "The LLM rejected the request as misconfigured (verify ENGINE_BASE_URL, ENGINE_MODEL, and ENGINE_API_KEY on the server)."
WEBHOOK_BLOCKED = "The webhook destination is not allowed by server policy."
WEBHOOK_REJECTED = "The webhook endpoint rejected the request (check the URL and credentials)."
WEBHOOK_REDIRECT = "The webhook endpoint returned a redirect; point the URL at its final destination."

# Transient failures (run state FAILED) ; retried until attempts run out.
TRANSIENT = "A temporary problem occurred while running this action; it will be retried."
TIMED_OUT = "The run exceeded its time limit and was reset to retry."
# Terminal: timed out AND ran out of retries (claim won't re-take it). NOT
# "will retry" ; that lie is exactly what this distinct message exists to avoid.
TIMED_OUT_EXHAUSTED = "The run repeatedly timed out and has exhausted its retry attempts."
# Terminal: a digest item kept failing to deliver and ran out of retries (the
# flush won't re-gather it). Mirrors TIMED_OUT_EXHAUSTED for the batch path.
TRANSIENT_EXHAUSTED = "This item repeatedly failed to deliver and has exhausted its retry attempts."

# Backfill-job `error` text (WatchActionBackfill.error), surfaced by
# `magpie backfill status`. Same no-leak policy; kept here so the
# backfill reaper + processor don't drift their own inline strings.
BACKFILL_TARGET_GONE = "The target action was removed before the backfill could run."
BACKFILL_WATCH_GONE = "The watch was removed before the backfill could run."
BACKFILL_TIMED_OUT = "The backfill exceeded its time limit and was reset to retry."
BACKFILL_TIMED_OUT_EXHAUSTED = "The backfill repeatedly timed out and has exhausted its retry attempts."
