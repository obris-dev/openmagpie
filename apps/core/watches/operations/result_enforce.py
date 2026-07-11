"""Shared result-schema enforcement for the terminal write paths.

Both terminal paths (the instant drain in `drain.py` and the digest flush in
`digest_flush.py`) persist a SUCCEEDED run's result blob. A plugin kind that
registered a result schema (`register_action(..., result=...)`) MUST carry a
conforming result so a consumer can rely on the shape; a mismatch is an action
defect, not a silently-stored bad blob. Normalizing here, before EITHER path writes,
is what makes that guarantee hold on both instant and digest runs rather than only
the instant ones.
"""

from __future__ import annotations

import logging

from openmagpie_schema.watch_enums import WatchActionRunState
from watches import registry as config_registry
from watches import run_messages
from watches.actions.protocol import ActionResult, OutboundActionResult

logger = logging.getLogger("watches")


def enforce_result_schema(kind: str, outcome: ActionResult, *, label: str) -> ActionResult:
    """Return `outcome` unchanged unless it is a SUCCEEDED result that violates
    `kind`'s registered result schema, in which case return an ERRORED result.

    `label` is a self-describing subject for the log line (the two callers run
    different granularities: the drain a single run, the digest flush a batch), used
    verbatim so the message is accurate on both paths.

    Preserves an `OutboundActionResult` subtype (carrying its `outbound` record) so
    the caller still writes the delivery audit row for a real POST (normalizing to a
    plain `ActionResult` would silently drop it). Any exception from the validator is
    treated as a result defect (immediate ERRORED), not a transient one, so it never
    escapes to a retry path. A no-op for a kind with no registered result schema
    (built-ins, result-less plugins) and for any non-SUCCEEDED state."""
    if outcome.state != WatchActionRunState.SUCCEEDED:
        return outcome
    try:
        config_registry.enforce_result(kind, outcome.result)
    except Exception:
        # Broad ON PURPOSE (unlike the narrow read-path fail-safes in serializers.py):
        # enforce_result runs a plugin-supplied result model, which can raise anything
        # (a buggy validator, not just PydanticValidationError). ANY failure is a
        # result defect, so contain it as a terminal ERRORED rather than let it escape
        # to the drain's retry path and burn attempts on a permanently-bad result.
        logger.exception("%s: result violates the %r result schema; marking ERRORED", label, kind)
        # Keep the violating blob on the ERRORED run for forensics (what shape failed).
        # Enforcement only runs for plugin kinds, whose run wire is PluginRunWire
        # (result: dict | None), so the bad blob renders on the audit read path.
        if isinstance(outcome, OutboundActionResult):
            return OutboundActionResult(
                state=WatchActionRunState.ERRORED,
                result=outcome.result,
                error=run_messages.RESULT_INVALID,
                outbound=outcome.outbound,
            )
        return ActionResult(state=WatchActionRunState.ERRORED, result=outcome.result, error=run_messages.RESULT_INVALID)
    return outcome
