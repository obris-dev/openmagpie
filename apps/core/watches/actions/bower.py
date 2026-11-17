"""BowerAction: deliver feed items to an OpenBower custom signal (kind=`bower`).

POSTs OpenBower's push envelope to the signal's webhook URL: one row per item,
its `external_id` the item's stable key and its cells the item fields the
config maps. The attempt and its status handling are `_http_delivery`'s.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from openmagpie_schema.watch_actions import BOWER_MAX_ROWS_PER_PUSH, BowerConfig, BowerPush, BowerResult, BowerRow
from openmagpie_schema.watch_enums import WatchActionKind, WatchActionRunState
from watches import run_messages
from watches.models import WatchAction

from ._config import load_typed
from ._http_delivery import HttpDelivery, deliver
from .protocol import Action, ActionContext, ActionItem, ActionResult

# A request_id is this prefix and this many hex characters of a digest.
REQUEST_ID_PREFIX = "magpie-"
REQUEST_ID_DIGEST_LENGTH = 32


class BowerAction(Action):
    """Delivers items to a custom signal as rows. `run` handles instant (one
    item) and digest (N items) alike."""

    kind = WatchActionKind.BOWER.value

    def run(self, action: WatchAction, *, items: list[ActionItem], context: ActionContext) -> ActionResult:
        config = load_typed(action, BowerConfig, log_label="bower")
        if config is None:
            return ActionResult(state=WatchActionRunState.ERRORED, error=run_messages.CONFIG_INVALID)
        push = build_push(str(action.id), config, items)
        delivery = HttpDelivery(
            action_id=str(action.id),
            log_label="bower",
            url=config.url,
            method="POST",
            headers={},
            body=push.model_dump(mode="json"),
            item_count=len(items),
        )
        return deliver(delivery, _result_for)


def build_push(action_id: str, config: BowerConfig, items: list[ActionItem]) -> BowerPush:
    """The envelope for a batch. Its request_id is derived from the action and
    the batch's item keys, so a resend of the same batch is the same request."""
    if len(items) > BOWER_MAX_ROWS_PER_PUSH:
        raise ValueError(f"a push carries at most {BOWER_MAX_ROWS_PER_PUSH} rows; got {len(items)}")
    rows = [BowerRow(external_id=it.key, cells=_cells(it.data, config.fields)) for it in items]
    return BowerPush(request_id=_request_id(action_id, items), rows=rows)


def _cells(item_data: dict[str, Any], fields: dict[str, str]) -> dict[str, str]:
    """Each mapped field the item holds as a scalar. A missing or empty field,
    or a list or object, leaves the cell out (the row lands with it blank)."""
    cells: dict[str, str] = {}
    for key, item_field in fields.items():
        value = item_data.get(item_field)
        if value is None or isinstance(value, (list, dict)):
            continue
        text = str(value)
        if text:
            cells[key] = text
    return cells


def _request_id(action_id: str, items: list[ActionItem]) -> str:
    keys = sorted(it.key for it in items)
    material = "\n".join([action_id, *keys])
    digest = hashlib.sha256(material.encode())
    return REQUEST_ID_PREFIX + digest.hexdigest()[:REQUEST_ID_DIGEST_LENGTH]


def _result_for(http_status: int, response: httpx.Response | None) -> dict[str, Any]:
    accepted = _accepted(response) if response is not None else None
    return BowerResult(http_status=http_status, accepted=accepted).model_dump(mode="json")


def _accepted(response: httpx.Response) -> int | None:
    """The row count OpenBower reports taking; None when its answer has none
    (a 2xx is a success whatever the body says)."""
    try:
        body = response.json()
    except ValueError:
        return None
    accepted = body.get("accepted") if isinstance(body, dict) else None
    return accepted if isinstance(accepted, int) else None
