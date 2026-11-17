"""One HTTP delivery attempt for the URL-bound delivery kinds (webhook, bower):
the send-time SSRF re-check, the call, and the status classification. Each
kind builds its own body and result; how a response maps to a run state is
the same for every destination.
  - 2xx                                  -> SUCCEEDED
  - 5xx / 408 / 429 / connect / timeout  -> FAILED (transient ; the run stays
    retryable, so a later attempt makes a NEW delivery row)
  - blocked destination / 3xx / other 4xx -> ERRORED (permanent)
Redirects are NOT followed (a redirect Location is an unvetted SSRF hop).

Every outcome returns an `OutboundActionResult` carrying an `OutboundCall`, so
the operations layer logs it as a WatchActionDelivery. Logs never carry the
URL (its path/query can carry a token).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from common.ssrf import destination_block_reason
from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages

from .protocol import OutboundActionResult, OutboundCall

logger = logging.getLogger("watches")

# 4xx statuses that ARE retryable despite being client errors: request
# timeout and rate-limit. Every other 4xx is a permanent misconfiguration
# (bad URL / auth / payload) the receiver won't accept on retry.
_RETRYABLE_4XX = frozenset({408, 429})

# A run result from the HTTP status and the response (None when none came back).
ResultFor = Callable[[int, httpx.Response | None], dict[str, Any]]


@dataclass(frozen=True)
class DeliveryMessages:
    """A kind's run errors for the permanent outcomes, in its own words (a
    run_messages constant each): a blocked destination, a redirect, and a
    refusal by status, since what a 4xx means depends on the receiver."""

    blocked: str
    redirect: str
    rejected: Callable[[int], str]


@dataclass(frozen=True)
class HttpDelivery:
    """What one attempt sends: where, how, and the body, already built."""

    action_id: str
    log_label: str
    url: str
    method: str
    headers: dict[str, str]
    body: dict[str, Any]
    item_count: int
    messages: DeliveryMessages


def deliver(delivery: HttpDelivery, result_for: ResultFor) -> OutboundActionResult:
    target_host = urlsplit(delivery.url).hostname or ""

    def make_call(http_status: int | None) -> OutboundCall:
        """The attempt record, shared across every outcome branch."""
        return OutboundCall(
            target_host=target_host,
            method=delivery.method,
            http_status=http_status,
            item_count=delivery.item_count,
            request_payload=delivery.body,
        )

    # Send-time SSRF re-check (resolve_dns=True): the write-time policy
    # only caught IP literals ; a hostname resolving to an internal
    # address is caught here. A blocked destination is permanent -> ERRORED.
    reason = destination_block_reason(
        delivery.url,
        require_https=settings.WEBHOOK_REQUIRE_HTTPS,
        block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
        resolve_dns=True,
    )
    if reason:
        logger.warning("%s: blocked destination for action=%s: %s", delivery.log_label, delivery.action_id, reason)
        return OutboundActionResult(
            state=WatchActionRunState.ERRORED, error=delivery.messages.blocked, outbound=make_call(None)
        )

    try:
        response = httpx.request(
            delivery.method,
            delivery.url,
            json=delivery.body,
            headers=delivery.headers,
            timeout=settings.WEBHOOK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _classify_status(delivery, exc.response, result_for, make_call)
    except httpx.HTTPError as exc:
        # Connect / timeout / etc: transient, no status. URL-free log (the
        # raw httpx str carries the url, a secret carrier).
        logger.warning("%s: action=%s transient %s", delivery.log_label, delivery.action_id, type(exc).__name__)
        return OutboundActionResult(
            state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT, outbound=make_call(None)
        )

    return OutboundActionResult(
        state=WatchActionRunState.SUCCEEDED,
        result=result_for(response.status_code, response),
        outbound=make_call(response.status_code),
    )


def _classify_status(
    delivery: HttpDelivery,
    response: httpx.Response,
    result_for: ResultFor,
    make_call: Callable[[int | None], OutboundCall],
) -> OutboundActionResult:
    """A non-2xx response -> the right result + an OutboundCall carrying the
    status. follow_redirects stays False, so a 3xx arrives here too."""
    status = response.status_code
    result = result_for(status, response)
    if 300 <= status < 400:
        # A redirect never reaches the receiver and re-redirects on retry:
        # a permanent misconfig (endpoint moved / wrong URL) -> ERRORED.
        logger.warning("%s: action=%s redirect %s (not followed)", delivery.log_label, delivery.action_id, status)
        return OutboundActionResult(
            state=WatchActionRunState.ERRORED,
            result=result,
            error=delivery.messages.redirect,
            outbound=make_call(status),
        )
    if 400 <= status < 500 and status not in _RETRYABLE_4XX:
        # Permanent client error (bad url / auth / payload): no retry.
        logger.warning("%s: action=%s permanent %s", delivery.log_label, delivery.action_id, status)
        return OutboundActionResult(
            state=WatchActionRunState.ERRORED,
            result=result,
            error=delivery.messages.rejected(status),
            outbound=make_call(status),
        )
    # Transient with a status (5xx / 408 / 429): retryable FAILED.
    logger.warning("%s: action=%s transient %s", delivery.log_label, delivery.action_id, status)
    return OutboundActionResult(
        state=WatchActionRunState.FAILED, error=run_messages.TRANSIENT, outbound=make_call(status)
    )
