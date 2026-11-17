"""What a bower connect answers when it does not complete."""

from __future__ import annotations

import httpx
from rest_framework import status as http_status

# Statuses that say OpenBower could not answer now (the ones a delivery
# retries), so trying again later may work and the request is not at fault.
_OUTAGE_4XX = frozenset({httpx.codes.REQUEST_TIMEOUT, httpx.codes.TOO_MANY_REQUESTS})


def is_outage(status: int) -> bool:
    return status >= httpx.codes.INTERNAL_SERVER_ERROR or status in _OUTAGE_4XX


class BowerConnectError(Exception):
    """A connect that did not complete. `status` is the HTTP status the
    endpoint answers with; the message is operator-facing and never carries
    the key or a webhook URL."""

    status = http_status.HTTP_400_BAD_REQUEST


class NoItemFields(BowerConnectError):
    pass


class BowerUnreachable(BowerConnectError):
    """OpenBower did not answer, or answered that it could not now."""

    status = http_status.HTTP_502_BAD_GATEWAY


class BowerRefused(BowerConnectError):
    """OpenBower answered the create with a refusal; the message carries its
    detail (a wrong key, a label it refused)."""


class BowerAnsweredOddly(BowerConnectError):
    status = http_status.HTTP_502_BAD_GATEWAY


class ActionNotAdded(BowerConnectError):
    """The signal exists in OpenBower but the action could not be added."""

    status = http_status.HTTP_409_CONFLICT
