"""WebhookAction: deliver feed items to an HTTP endpoint (kind=`webhook`).

Sends one self-describing `WebhookPayload` (watch + window + per-item source)
to the configured URL with the configured method (POST | PUT | PATCH). Instant
is a one-item batch ; digest is N items in one window: ONE payload shape for
both. Status handling:
  - 2xx                                  -> SUCCEEDED
  - 5xx / 408 / 429 / connect / timeout  -> FAILED (transient ; the run stays
    retryable, so a later attempt makes a NEW delivery row)
  - blocked destination / 3xx / other 4xx -> ERRORED (permanent)
Redirects are NOT followed (a redirect Location is an unvetted SSRF hop).

Every attempt that reaches the network returns an `OutboundActionResult` (even
failures) carrying an `OutboundCall`, so the operations layer logs it as a
WatchActionDelivery. The body is URL-free in logs (the URL path/query can carry
a token).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from common.ssrf import destination_block_reason
from openmagpie_schema.watch_actions import (
    WebhookConfig,
    WebhookItem,
    WebhookPayload,
    WebhookResult,
    WebhookSource,
    WebhookWatchRef,
    WebhookWindow,
)
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._config import load_typed
from .protocol import (
    Action,
    ActionContext,
    ActionItem,
    ActionResult,
    OutboundActionResult,
    OutboundCall,
)

logger = logging.getLogger("watches")

# 4xx statuses that ARE retryable despite being client errors: request
# timeout and rate-limit. Every other 4xx is a permanent misconfiguration
# (bad URL / auth / payload) the receiver won't accept on retry.
_RETRYABLE_4XX = frozenset({408, 429})


class WebhookAction(Action):
    """Delivers items to a URL ; classifies the HTTP response into the run
    result and records the call. `run` handles instant (one item) and digest
    (N items) alike."""

    kind = WatchActionKind.WEBHOOK.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        config = load_typed(action, WebhookConfig, log_label="webhook")
        if config is None:
            # No call was made: a base result, no OutboundCall to log.
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        payload = _build_payload(action, config, items, context)
        return self._deliver(action, config, items, payload)

    def _deliver(
        self, action: WatchAction, config: WebhookConfig, items: list[ActionItem], payload: WebhookPayload
    ) -> OutboundActionResult:
        body = payload.model_dump(mode="json")
        target_host = urlsplit(config.url).hostname or ""

        def make_call(http_status: int | None) -> OutboundCall:
            """The attempt record, shared across every outcome branch."""
            return OutboundCall(
                target_host=target_host,
                method=config.method.value,
                http_status=http_status,
                item_count=len(items),
                request_payload=body,
            )

        # Send-time SSRF re-check (resolve_dns=True): the write-time policy
        # only caught IP literals ; a hostname resolving to an internal
        # address is caught here. A blocked destination is permanent -> ERRORED.
        reason = destination_block_reason(
            config.url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            resolve_dns=True,
        )
        if reason:
            logger.warning("webhook: blocked destination for action=%s: %s", action.id, reason)
            return OutboundActionResult(
                state=WatchActionRunState.ERRORED, error=run_messages.WEBHOOK_BLOCKED, outbound=make_call(None)
            )

        # Dedup is uniform across cadences: every item carries its `key` in the
        # body (instant is a one-item batch), so there's no separate header.
        headers = dict(config.headers)
        try:
            response = httpx.request(
                config.method.value, config.url, json=body, headers=headers, timeout=settings.WEBHOOK_TIMEOUT_SECONDS
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return self._classify_status(action, exc.response.status_code, make_call)
        except httpx.HTTPError as exc:
            # Connect / timeout / etc: transient, no status. URL-free log (the
            # raw httpx str carries the url, a secret carrier).
            logger.warning("webhook: action=%s transient %s", action.id, type(exc).__name__)
            return OutboundActionResult(
                state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT, outbound=make_call(None)
            )

        return OutboundActionResult(
            state=WatchActionRunState.SUCCEEDED,
            result=WebhookResult(http_status=response.status_code).model_dump(mode="json"),
            outbound=make_call(response.status_code),
        )

    def _classify_status(
        self, action: WatchAction, status: int, make_call: Callable[[int | None], OutboundCall]
    ) -> OutboundActionResult:
        """A non-2xx response -> the right result + an OutboundCall carrying the
        status. follow_redirects stays False, so a 3xx arrives here too."""
        result = WebhookResult(http_status=status).model_dump(mode="json")
        if 300 <= status < 400:
            # A redirect never reaches the receiver and re-redirects on retry:
            # a permanent misconfig (endpoint moved / wrong URL) -> ERRORED.
            logger.warning("webhook: action=%s redirect %s (not followed)", action.id, status)
            return OutboundActionResult(
                state=WatchActionRunState.ERRORED,
                result=result,
                error=run_messages.WEBHOOK_REDIRECT,
                outbound=make_call(status),
            )
        if 400 <= status < 500 and status not in _RETRYABLE_4XX:
            # Permanent client error (bad url / auth / payload): no retry.
            logger.warning("webhook: action=%s permanent %s", action.id, status)
            return OutboundActionResult(
                state=WatchActionRunState.ERRORED,
                result=result,
                error=run_messages.WEBHOOK_REJECTED,
                outbound=make_call(status),
            )
        # Transient with a status (5xx / 408 / 429): retryable FAILED.
        logger.warning("webhook: action=%s transient %s", action.id, status)
        return OutboundActionResult(
            state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT, outbound=make_call(status)
        )


def _build_payload(
    action: WatchAction, config: WebhookConfig, items: list[ActionItem], context: ActionContext
) -> WebhookPayload:
    """The self-describing body for both cadences: watch ref, cadence, the
    digest window (None for instant), and the per-item key/source plus the
    field-filtered item body."""
    window = (
        WebhookWindow(since=context.window_since, until=context.window_until)
        if context.window_since is not None and context.window_until is not None
        else None
    )
    return WebhookPayload(
        watch=WebhookWatchRef(id=context.watch_id, name=context.watch_name),
        action_id=str(action.id),
        delivery=context.delivery,
        window=window,
        items=[
            WebhookItem(
                key=it.key,
                source=WebhookSource(
                    label=it.source_label, kind=it.source_kind, pattern_id=it.source_meta.get("pattern_id")
                ),
                item=_filtered(it.data, config.include_fields),
            )
            for it in items
        ],
    )


def _filtered(item_data: dict, include_fields: list[str]) -> dict[str, Any]:
    """The item dict, narrowed to `include_fields` (empty = send all).
    Unknown field names are silently skipped (the whitelist is advisory)."""
    if not include_fields:
        return item_data
    return {k: item_data[k] for k in include_fields if k in item_data}
