"""ExtractAction: LLM hydration of declared fields (kind=`extract`).

Pulls the action's declared fields out of one feed item with the configured
engine and writes them, structured, to the run result. Pure hydration: it
gates nothing and delivers nothing, so a clean run always SUCCEEDS and the
chain advances. Mirrors SemanticFilterAction's guard structure (the same
permanent-defect cases ERROR; transient engine failures propagate -> FAILED).
"""

from __future__ import annotations

import logging

from engine.engines import EngineRequestRejected
from openmagpie_schema.watch_actions import ExtractConfig, ExtractField, ExtractResult, ExtractStatus
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._engine_action import EngineActionMixin
from .protocol import Action, ActionContext, ActionItem, ActionResult

logger = logging.getLogger("watches")


class ExtractAction(EngineActionMixin, Action):
    """Runs the engine's field extraction against an item; always SUCCEEDS."""

    kind = WatchActionKind.EXTRACT.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        # Shared prelude (load config, hydrate item, resolve engine, fetch external
        # content); an ActionResult here is a permanent-defect ERROR, returned as-is.
        prepared = self._prepare(action, items)
        if isinstance(prepared, ActionResult):
            return prepared
        config = prepared.config
        assert isinstance(config, ExtractConfig)  # narrow to extract's declared fields

        # A 4xx proving a permanent request/config defect is ERRORED, not retried.
        # Transient extract failures (engine down, rate-limited, malformed JSON)
        # still propagate -> the drain's FAILED.
        try:
            extraction = prepared.engine.extract(
                prepared.payload,
                fields=config.fields,
                instructions=config.instructions,
                model=config.engine.model or None,
                external_content=prepared.external_content,
            )
        except EngineRequestRejected as exc:
            logger.error("extract: engine rejected request for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_REJECTED)

        result = ExtractResult(
            extracted=extraction.extracted,
            status=_extract_status(extraction.extracted, fields=config.fields),
            enrichment_status=prepared.enrichment_status,
        )
        # Pure hydration: never gates, never errors on an empty extraction. The run
        # SUCCEEDS so the chain advances and downstream / the report see the result.
        return ActionResult(state=WatchActionRunState.SUCCEEDED, result=result.model_dump(mode="json"))


def _extract_status(extracted: dict[str, str], *, fields: list[ExtractField]) -> ExtractStatus:
    """COMPLETE when every DECLARED field came back non-empty, EMPTY when none did,
    else PARTIAL. Counts the DECLARED field NAMES (not the engine's return shape), so
    an extra / missing key from a non-strict backend can't skew the label."""
    filled = sum(1 for f in fields if extracted.get(f.name, "").strip())
    if filled == 0:
        return ExtractStatus.EMPTY
    if filled == len(fields):
        return ExtractStatus.COMPLETE
    return ExtractStatus.PARTIAL
