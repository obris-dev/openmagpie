"""EngineActionMixin: the shared prepare path for the LLM-backed action kinds
(semantic_filter, extract).

The two actions differ only at the engine CALL and the result build; everything
before -- load + validate config, hydrate the one item, resolve the engine, fetch
the optional linked article -- is identical and the same permanent-defect cases
ERROR. That prelude lives here ONCE so a fix to one error path can't drift the
other. `_prepare` returns the gathered inputs on success, or an ERRORED
`ActionResult` for a permanent defect (never retried); the transient engine
failure stays on the caller's engine call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import ValidationError

from engine import registry as engine_registry
from engine.engines import Engine
from openmagpie_schema.watch_actions import EngineActionConfigBase, ExternalContentStatus
from openmagpie_schema.watch_enums import WatchActionRunState
from sources.payload_registry import UnhydrateablePayload, hydrate_data
from sources.payloads import SourcePayload
from watches import run_messages
from watches.models import WatchAction
from watches.registry import load_config

from ._external import resolve_external_content
from ._fetch import ExternalFetchMixin
from .protocol import ActionItem, ActionResult

logger = logging.getLogger("watches")


@dataclass(frozen=True)
class _Prepared:
    """The inputs an LLM-backed action's engine call needs, common to both kinds.
    `config` is the loaded `EngineActionConfigBase`; the action narrows it to its
    own kind for the kind-specific fields (threshold / declared fields)."""

    config: EngineActionConfigBase
    payload: SourcePayload
    engine: Engine
    external_content: str | None
    enrichment_status: ExternalContentStatus


class EngineActionMixin(ExternalFetchMixin):
    """The shared prelude for an LLM-backed action's `run`; `kind` (set by the
    concrete Action) labels the log lines."""

    kind: str

    def _prepare(self, action: WatchAction, items: list[ActionItem]) -> _Prepared | ActionResult:
        """Load + validate config, hydrate the one item, resolve the engine, and
        fetch the optional linked article. On success -> `_Prepared`. A PERMANENT
        defect (bad config / unreadable item / unknown engine) -> an ERRORED
        `ActionResult` (terminal, never retried): retrying re-parses the same bad
        blob / can't rehydrate / can't register the kind. Transient engine failures
        are the caller's to handle on the engine call itself (-> the drain's FAILED)."""
        assert len(items) == 1, f"{self.kind} runs one item at a time"
        try:
            config = load_config(action)
        except ValidationError as exc:
            logger.exception("%s: invalid config for action=%s: %s", self.kind, action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        assert isinstance(config, EngineActionConfigBase)  # the LLM kinds share this base
        try:
            payload = hydrate_data(items[0].data)
        except UnhydrateablePayload as exc:
            logger.exception("%s: unhydrateable item for action=%s: %s", self.kind, action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ITEM_UNREADABLE)
        # Empty engine.kind = the server default (resolved here, not stored). An
        # unregistered kind is a PERMANENT config/deploy defect -> ERRORED; the
        # engine call's OWN errors (down, bad JSON) still propagate -> FAILED.
        try:
            engine = engine_registry.get(config.engine.kind)
        except KeyError:
            logger.warning("%s: unknown engine kind=%r for action=%s", self.kind, config.engine.kind, action.id)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_UNAVAILABLE)
        # Opt-in: fetch the item's external link and fold its text into the call
        # (best-effort). No-op when off or the item has no external link.
        external_content, enrichment_status = resolve_external_content(
            self.fetch_external_url,
            action_id=str(action.id),
            enabled=config.fetch_external_content,
            article_url=payload.article_url,  # per-kind: external_url for aggregators, url for RSS
        )
        return _Prepared(
            config=config,
            payload=payload,
            engine=engine,
            external_content=external_content,
            enrichment_status=enrichment_status,
        )
