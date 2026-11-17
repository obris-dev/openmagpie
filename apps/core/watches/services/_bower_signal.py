"""An existing OpenBower signal, read from its webhook URL: the signal's id
(in the URL's path) and its columns (a GET on the URL answers them, the token
in the path the only credential), matched to a watch's item fields by label.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from openmagpie_schema.watch import BowerConnectColumn
from openmagpie_schema.watch_actions import BOWER_URL_GONE_STATUSES
from openmagpie_schema.watch_enums import BowerColumnType

from . import _bower_http
from ._bower_errors import BowerAnsweredOddly, BowerRefused, BowerUnreachable, is_outage

logger = logging.getLogger("watches")

# A signal's webhook URL path: /v1/signals/<id>/ingest/<token>.
_WEBHOOK_PATH = re.compile(r"/v1/signals/(?P<signal_id>[^/]+)/ingest/[^/]+/?$")

# A field's derived type -> the column types that take its values. A text
# column takes anything; a date column keeps a timestamp's day.
_TAKES: dict[str, set[str]] = {
    BowerColumnType.TEXT: {BowerColumnType.TEXT},
    BowerColumnType.NUMBER: {BowerColumnType.NUMBER, BowerColumnType.TEXT},
    BowerColumnType.URL: {BowerColumnType.URL, BowerColumnType.TEXT},
    BowerColumnType.DATETIME: {BowerColumnType.DATETIME, BowerColumnType.DATE, BowerColumnType.TEXT},
}


class _SignalColumn(BaseModel):
    key: str
    label: str
    type: str


class _SignalSchema(BaseModel):
    """The part of OpenBower's push schema a connect reads."""

    columns: list[_SignalColumn]


@dataclass(frozen=True)
class SignalMatch:
    """How a watch's item fields fill an existing signal: `columns` lists
    every signal column (`field` empty for one left blank), `column_map` is the
    action's column key -> item field map, and `unmatched_fields` the item
    fields no column takes."""

    columns: list[BowerConnectColumn]
    column_map: dict[str, str]
    unmatched_fields: list[str]


def signal_id_of(webhook_url: str) -> str | None:
    """The signal id in a webhook URL's path, or None when the path is not
    a signal's webhook URL."""
    found = _WEBHOOK_PATH.search(urlsplit(webhook_url).path)
    return found.group("signal_id") if found else None


def read_columns(webhook_url: str) -> list[_SignalColumn]:
    """The signal's pushable columns, from a GET on its webhook URL."""
    _bower_http.check(webhook_url, field="webhook_url")
    try:
        response = _bower_http.get(webhook_url, field="webhook_url")
    except httpx.HTTPError as exc:
        logger.warning("bower connect: schema read failed %s", type(exc).__name__)
        raise BowerUnreachable(f"OpenBower did not answer at the webhook URL ({type(exc).__name__})") from exc
    status = response.status_code
    if is_outage(status):
        raise BowerUnreachable(f"OpenBower could not answer at the webhook URL ({status}); try again later")
    if status in BOWER_URL_GONE_STATUSES:
        raise BowerRefused(
            f"OpenBower refused the webhook URL ({status}); "
            "copy the signal's current URL from OpenBower (a rotated one stops working)"
        )
    if status != httpx.codes.OK:
        raise BowerRefused(f"OpenBower refused the webhook URL ({status})")
    try:
        return _SignalSchema.model_validate(response.json()).columns
    except (ValueError, ValidationError) as exc:
        raise BowerAnsweredOddly("OpenBower's answer at the webhook URL was not a signal's columns") from exc


def match(columns: list[_SignalColumn], item_fields: list[tuple[str, str, str]]) -> SignalMatch:
    """Each signal column filled by the item field whose label matches its
    own (case aside) and whose values its type takes. `item_fields` is
    (field, label, type) per field the watch's sources produce."""
    by_label = {label.casefold(): (field, field_type) for field, label, field_type in item_fields}
    answered: list[BowerConnectColumn] = []
    column_map: dict[str, str] = {}
    for column in columns:
        field = ""
        found = by_label.get(column.label.casefold())
        if found is not None and column.type in _TAKES.get(found[1], set()):
            field = found[0]
            column_map[column.key] = field
        answered.append(BowerConnectColumn(key=column.key, label=column.label, type=column.type, field=field))
    used = set(column_map.values())
    unmatched = [field for field, _label, _type in item_fields if field not in used]
    return SignalMatch(columns=answered, column_map=column_map, unmatched_fields=unmatched)
