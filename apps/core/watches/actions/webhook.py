"""WebhookAction: deliver feed items to an HTTP endpoint (kind=`webhook`).

Sends one self-describing `WebhookPayload` (watch + window + per-item source)
to the configured URL with the configured method (POST | PUT | PATCH). Instant
is a one-item batch ; digest is N items in one window: ONE payload shape for
both. The attempt and its status handling are `_http_delivery`'s.
"""

from __future__ import annotations

from typing import Any

import httpx

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
from ._http_delivery import DeliveryMessages, HttpDelivery, deliver
from .protocol import (
    Action,
    ActionContext,
    ActionItem,
    ActionResult,
)


class WebhookAction(Action):
    """Delivers items to a URL ; the response sets the run's result and the
    call is recorded. `run` handles instant (one item) and digest (N items)
    alike."""

    kind = WatchActionKind.WEBHOOK.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        config = load_typed(action, WebhookConfig, log_label="webhook")
        if config is None:
            # No call was made: a base result, no OutboundCall to log.
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        payload = _build_payload(action, config, items, context)
        # Dedup is uniform across cadences: every item carries its `key` in the
        # body (instant is a one-item batch), so there's no separate header.
        delivery = HttpDelivery(
            action_id=str(action.id),
            log_label="webhook",
            url=config.url,
            method=config.method.value,
            headers=dict(config.headers),
            body=payload.model_dump(mode="json"),
            item_count=len(items),
            messages=_MESSAGES,
        )
        return deliver(delivery, _result_for)


_MESSAGES = DeliveryMessages(
    blocked=run_messages.WEBHOOK_BLOCKED,
    redirect=run_messages.WEBHOOK_REDIRECT,
    rejected=lambda _status: run_messages.WEBHOOK_REJECTED,
)


def _result_for(http_status: int, _response: httpx.Response | None) -> dict[str, Any]:
    return WebhookResult(http_status=http_status).model_dump(mode="json")


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
