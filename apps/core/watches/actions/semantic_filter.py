"""SemanticFilterAction: the LLM relevance gate (kind=`semantic_filter`).

Scores one feed item against the action's `instructions` with the
configured engine ; the run SUCCEEDS (chain advances) when the score
meets `threshold`, else GATES (chain stops). Ports the v1 listener
judgment into the v2 action interface.
"""

from __future__ import annotations

import logging

from engine.engines import EngineRequestRejected
from openmagpie_schema.watch_actions import SemanticFilterConfig, SemanticFilterResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._engine_action import EngineActionMixin
from .protocol import Action, ActionContext, ActionItem, ActionResult

logger = logging.getLogger("watches")


class SemanticFilterAction(EngineActionMixin, Action):
    """Runs the relevance engine against an item and gates on the score."""

    kind = WatchActionKind.SEMANTIC_FILTER.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        # Shared prelude (load config, hydrate item, resolve engine, fetch external
        # content); an ActionResult here is a permanent-defect ERROR, returned as-is.
        prepared = self._prepare(action, items)
        if isinstance(prepared, ActionResult):
            return prepared
        config = prepared.config
        assert isinstance(config, SemanticFilterConfig)  # narrow to the filter's threshold

        # A 4xx that proves a permanent request/config defect (bad auth, missing
        # endpoint/model, malformed request) is ERRORED, not retried. Transient
        # judge failures (engine down, rate-limited, malformed JSON) still
        # propagate -> the drain's FAILED.
        try:
            judgment = prepared.engine.judge(
                prepared.payload,
                instructions=config.instructions,
                model=config.engine.model or None,
                external_content=prepared.external_content,
            )
        except EngineRequestRejected as exc:
            logger.error("semantic_filter: engine rejected request for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_REJECTED)

        passed = judgment.score >= config.threshold
        result = SemanticFilterResult(
            passed=passed, score=judgment.score, reason=judgment.reason, enrichment_status=prepared.enrichment_status
        )
        state = WatchActionRunState.SUCCEEDED if passed else WatchActionRunState.GATED
        return ActionResult(state=state, result=result.model_dump(mode="json"))
