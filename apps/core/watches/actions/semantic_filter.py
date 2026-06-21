"""SemanticFilterAction: the LLM relevance gate (kind=`semantic_filter`).

Scores one feed item against the action's `instructions` with the
configured engine ; the run SUCCEEDS (chain advances) when the score
meets `threshold`, else GATES (chain stops). Ports the v1 listener
judgment into the v2 action interface.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from engine import registry as engine_registry
from engine.engines import EngineRequestRejected
from openmagpie_schema.watch_actions import ExternalContentStatus, SemanticFilterConfig, SemanticFilterResult
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from sources.connectors.base import ConnectorParseError, extract_article_text
from sources.payload_registry import UnhydrateablePayload, hydrate_data
from sources.payloads import SourcePayload
from watches import run_messages
from watches.models import WatchAction
from watches.registry import load_config

from ._fetch import ExternalFetchMixin
from .protocol import Action, ActionContext, ActionItem, ActionResult

logger = logging.getLogger("watches")

# Bounds for the opt-in external-article fetch (config.fetch_external_content).
MAX_ARTICLE_BYTES = 5 * 1024 * 1024
ARTICLE_USER_AGENT = "openmagpie/1.0 (+https://github.com/obris-dev/openmagpie)"


class SemanticFilterAction(ExternalFetchMixin, Action):
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
        # Opt-in: fetch the item's external link and fold its text into the judge
        # (best-effort; see _external_content). No-op when off or the item has no
        # external link.
        external_content, enrichment_status = self._external_content(action.id, config, payload)
        # A 4xx that proves a permanent request/config defect (bad auth, missing
        # endpoint/model, malformed request) is ERRORED, not retried - like the
        # unknown-kind path above. Transient judge failures (engine down,
        # rate-limited, malformed JSON) still propagate -> the drain's FAILED.
        try:
            judgment = engine.judge(
                payload,
                instructions=config.instructions,
                model=config.engine.model or None,
                external_content=external_content,
            )
        except EngineRequestRejected as exc:
            logger.error("semantic_filter: engine rejected request for action=%s: %s", action.id, exc)
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.ENGINE_REJECTED)

        passed = judgment.score >= config.threshold
        result = SemanticFilterResult(
            passed=passed, score=judgment.score, reason=judgment.reason, enrichment_status=enrichment_status
        )
        state = WatchActionRunState.SUCCEEDED if passed else WatchActionRunState.GATED
        return ActionResult(state=state, result=result.model_dump(mode="json"))

    def _external_content(
        self, action_id: str, config: SemanticFilterConfig, payload: SourcePayload
    ) -> tuple[str | None, ExternalContentStatus]:
        """Lazily fetch + extract the item's external link for the judge. Returns
        (content, status): the status is recorded on the result so a judgment
        carries whether it saw the article, and if not, why. Best-effort by design
        -- a failed fetch (UNAVAILABLE) or an empty extraction (MISSING) still lets
        the judge run on title + content ; the run is never failed for missing
        enrichment. Only the EXPECTED fetch failures are swallowed (httpx /
        ConnectorParseError: blocked host, oversize, timeout, 4xx/5xx) ; an
        unexpected error propagates, since that is a real defect."""
        if not config.fetch_external_content:
            return None, ExternalContentStatus.DISABLED
        if not payload.external_url:
            return None, ExternalContentStatus.NOT_APPLICABLE
        try:
            html = self.fetch_external_url(
                payload.external_url, max_bytes=MAX_ARTICLE_BYTES, user_agent=ARTICLE_USER_AGENT
            )
        except (httpx.HTTPError, ConnectorParseError) as exc:
            # warning (not info): a fetch FAILURE can signal a systemic egress /
            # network problem, not just one dead link. (The per-run UNAVAILABLE
            # status is the primary signal; MISSING / paywalls stay unlogged.)
            logger.warning(
                "semantic_filter: external fetch failed for action=%s url=%s: %s",
                action_id,
                payload.external_url,
                exc,
            )
            return None, ExternalContentStatus.UNAVAILABLE
        text = extract_article_text(html)
        if not text:
            # Fetched OK but no usable article text (paywall / JS-only / non-article).
            return None, ExternalContentStatus.MISSING
        return text, ExternalContentStatus.INCLUDED
