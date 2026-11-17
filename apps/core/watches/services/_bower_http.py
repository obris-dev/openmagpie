"""The outbound calls a bower connect makes to OpenBower (the schema read on a
webhook URL, the create on an API URL), both to an operator-given URL and
both synchronous on the request.

`check` refuses a URL the WEBHOOK_* policy blocks. `get` and `post` then make
the call; when private addresses are blocked they go through the pinned-IP
transport (`common.safe_http`), so the host is resolved once and connected
to as checked, closing the window where a second lookup answers otherwise.
With private addresses allowed (a local OpenBower) there is nothing to pin.
Redirects are not followed.
"""

from __future__ import annotations

from typing import Any

import httpx
from django.conf import settings

from common.safe_http import SsrfBlocked, pinned_transport
from common.ssrf import destination_block_reason

from ._bower_errors import BowerConnectError


def check(url: str, *, field: str) -> None:
    reason = destination_block_reason(
        url,
        require_https=settings.WEBHOOK_REQUIRE_HTTPS,
        block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
        resolve_dns=True,
    )
    if reason:
        raise BowerConnectError(f"{field} rejected: {reason}")


def get(url: str, *, field: str, **kwargs: Any) -> httpx.Response:
    return _send("GET", url, field=field, **kwargs)


def post(url: str, *, field: str, **kwargs: Any) -> httpx.Response:
    return _send("POST", url, field=field, **kwargs)


def _send(method: str, url: str, *, field: str, **kwargs: Any) -> httpx.Response:
    transport = pinned_transport() if settings.WEBHOOK_BLOCK_PRIVATE_IPS else None
    try:
        with httpx.Client(transport=transport, timeout=settings.WEBHOOK_TIMEOUT_SECONDS) as client:
            return client.request(method, url, **kwargs)
    except SsrfBlocked as exc:
        raise BowerConnectError(f"{field} rejected: it resolves to a blocked address") from exc
