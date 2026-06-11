"""SemanticFilterAction: the LLM relevance gate (kind=`semantic_filter`).

Scores one feed item against the action's `instructions` with the
configured engine ; the run SUCCEEDS (chain advances) when the score
meets `threshold`, else GATES (chain stops). Ports the v1 listener
judgment into the v2 action interface.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from engine import registry as engine_registry
from engine.engines import EngineRequestRejected
from openmagpie_schema.watch_actions import SemanticFilterConfig, SemanticFilterResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from sources.payload_registry import UnhydrateablePayload, hydrate_data
from watches import run_messages
from watches.models import WatchAction
from watches.registry import load_config

from .protocol import Action, ActionContext, ActionItem, ActionResult

logger = logging.getLogger("watches")


class SemanticFilterAction(Action):
    """Runs the relevance engine against an item and gates on the score."""

    kind = WatchActionKind.SEMANTIC_FILTER.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        # A filter judges exactly one item ; it is never digested (no batch).
        assert len(items) == 1, "semantic_filter runs one item at a time"
        item_data = items[0].data
        # A corrupt / schema-drifted stored config is a PERMANENT defect:
        # retrying re-parses the same bad blob. ERROR it (terminal) rather
        # than let it propagate to the drain's catch-all -> FAILED, which
        # would burn the whole retry budget with a "will be retried" lie.
        try:
            config = load_config(action)
        except ValidationError as exc:
            logger.exception("semantic_filter: invalid config for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        assert isinstance(config, SemanticFilterConfig)  # registry guarantees by kind

        # A stored item that can't be rehydrated is a PERMANENT backend
        # defect (renamed/removed connector, schema drift) ; retrying can't
        # help, so ERROR it (terminal, never retried, chain stops) rather
        # than FAILED (which would burn retries on something unfixable).
        try:
            payload = hydrate_data(item_data)
        except UnhydrateablePayload as exc:
            # Full traceback to the log (sentry / server) ; a permanent
            # defect worth triaging (a broken connector / schema drift) ;
            # the run carries only the sanitized note, no exception detail.
            logger.exception("semantic_filter: unhydrateable item for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ITEM_UNREADABLE)

        # Empty engine.kind = use the server default (resolved here, not
        # stored, so the deploy default applies at run time). An unregistered
        # kind is a PERMANENT config/deploy defect (like the drain's
        # no-executor case) -> ERRORED, not a retryable FAILED. The judge
        # call's OWN errors (httpx, bad JSON) still propagate -> FAILED,
        # since an engine being down IS transient.
        try:
            engine = engine_registry.get(config.engine.kind)
        except KeyError:
            logger.warning("semantic_filter: unknown engine kind=%r for action=%s", config.engine.kind, action.id)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_UNAVAILABLE)
        # A 4xx that proves a permanent request/config defect (bad auth, missing
        # endpoint/model, malformed request) is ERRORED, not retried - like the
        # unknown-kind path above. Transient judge failures (engine down,
        # rate-limited, malformed JSON) still propagate -> the drain's FAILED.
        try:
            judgment = engine.judge(payload, instructions=config.instructions, model=config.engine.model or None)
        except EngineRequestRejected as exc:
            logger.error("semantic_filter: engine rejected request for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_REJECTED)

        passed = judgment.score >= config.threshold
        result = SemanticFilterResult(passed=passed, score=judgment.score, reason=judgment.reason)
        state = WatchActionRunState.SUCCEEDED if passed else WatchActionRunState.GATED
        return ActionResult(state=state, result=result.model_dump(mode="json"))
