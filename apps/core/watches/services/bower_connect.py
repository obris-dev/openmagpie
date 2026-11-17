"""Connect a watch to OpenBower: create a custom signal whose columns are the
watch's item fields, then add the `bower` action that delivers to it.

The OpenBower API key is used for that one create and never stored, logged,
or answered with: the action keeps only the signal's webhook URL, which
OpenBower can rotate on its own.
"""

from __future__ import annotations

import logging
import types
import typing
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any

import httpx
from django.conf import settings
from pydantic import BaseModel, ValidationError

from common.ssrf import destination_block_reason
from feeds.services import FeedService, SourceService
from openmagpie_schema.watch import BowerConnectColumn, BowerConnectInput, build_watch_action_input
from openmagpie_schema.watch_actions import BowerConfig
from openmagpie_schema.watch_enums import WatchActionKind
from sources.payload_registry import class_for_source
from sources.payloads import SourcePayload
from watches.models import Watch, WatchAction
from watches.policy import PolicyError

from .watches import WatchService
from .watches._actions import ConcurrentChainError

logger = logging.getLogger("watches")

# OpenBower column types the derived columns use. Every value Magpie sends
# must fit its column's type, or OpenBower refuses the whole push, so a
# timestamp is `text` (OpenBower's `date` takes a bare YYYY-MM-DD) and only
# an integer is a `number`.
TEXT = "text"
NUMBER = "number"
URL = "url"

# Item fields that are never a column: the row's own id rides as its
# external_id, and the rest name the item's plumbing, not its content.
_NOT_COLUMNS = frozenset({"kind", "external_id", "source", "parent_external_id"})
# String fields holding a link, so OpenBower renders them as one.
_URL_FIELDS = frozenset({"url", "external_url", "permalink", "feed_url"})
# The fields every source shares lead, in this order; each source's own follow.
_LEADING_FIELDS = ("title", "content", "url", "external_url", "author", "occurred_at")

SIGNALS_PATH = "/v1/signals"
CUSTOM_SIGNAL_TYPE = "custom"


class BowerConnectError(Exception):
    """A connect that did not complete. `status` is the HTTP status the
    endpoint answers with; the message is operator-facing and never carries
    the key or a webhook URL."""

    status = 400


class NoItemFields(BowerConnectError):
    pass


class BowerUnreachable(BowerConnectError):
    status = 502


class BowerRefused(BowerConnectError):
    """OpenBower answered the create with a refusal; the message carries its
    detail (a wrong key, a label it refused)."""


class BowerAnsweredOddly(BowerConnectError):
    status = 502


class ActionNotAdded(BowerConnectError):
    """The signal exists in OpenBower but the action could not be added."""

    status = 409


@dataclass(frozen=True)
class DerivedColumn:
    field: str
    label: str
    type: str


class _CreatedColumn(BaseModel):
    key: str
    label: str
    type: str


class _CreatedSheet(BaseModel):
    id: str
    label: str
    columns: list[_CreatedColumn]


class _CreatedSignal(BaseModel):
    """The part of OpenBower's create answer a connect reads."""

    sheet: _CreatedSheet
    webhook_url: str


@dataclass(frozen=True)
class BowerConnection:
    signal_id: str
    signal_label: str
    columns: list[BowerConnectColumn]
    action: WatchAction


class BowerConnectService:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    @cached_property
    def watches(self) -> WatchService:
        return WatchService(account_id=self.account_id)

    @cached_property
    def feeds(self) -> FeedService:
        return FeedService(account_id=self.account_id)

    @cached_property
    def sources(self) -> SourceService:
        return SourceService(account_id=self.account_id)

    def columns_for(self, watch: Watch) -> list[DerivedColumn]:
        """One column per scalar item field the watch's sources produce: the
        fields every source shares, then each source kind's own."""
        kinds: list[str] = []
        for feed_id in self.watches.feed_ids(watch):
            feed = self.feeds.get(feed_id)
            for source in self.sources.list(feed):
                if source.kind not in kinds:
                    kinds.append(source.kind)
        if not kinds:
            raise NoItemFields("the watch's feeds have no sources, so there are no item fields to send")
        fields: dict[str, str] = _scalar_fields(SourcePayload)
        for kind in kinds:
            payload = class_for_source(kind)
            if payload is not None:
                for name, column_type in _scalar_fields(payload).items():
                    fields.setdefault(name, column_type)
        names = [name for name in _LEADING_FIELDS if name in fields]
        names += [name for name in fields if name not in names]
        return [DerivedColumn(field=name, label=_label(name), type=fields[name]) for name in names]

    def preview(self, watch: Watch, request: BowerConnectInput) -> tuple[str, list[BowerConnectColumn]]:
        """What a connect would create, without calling OpenBower: the
        signal's label and its columns (no keys yet)."""
        columns = [
            BowerConnectColumn(label=column.label, type=column.type, field=column.field)
            for column in self.columns_for(watch)
        ]
        return request.label or watch.name, columns

    def connect(self, watch: Watch, request: BowerConnectInput) -> BowerConnection:
        columns = self.columns_for(watch)
        created = self._create_signal(request, label=request.label or watch.name, columns=columns)
        by_label = {column.label: column for column in created.sheet.columns}
        fields: dict[str, str] = {}
        answered: list[BowerConnectColumn] = []
        for column in columns:
            made = by_label.get(column.label)
            if made is None:
                raise BowerAnsweredOddly(f"OpenBower created the signal without a {column.label!r} column")
            fields[made.key] = column.field
            answered.append(BowerConnectColumn(key=made.key, label=made.label, type=made.type, field=column.field))
        config = BowerConfig(
            url=created.webhook_url,
            fields=fields,
            delivery=request.delivery,
            digest_interval_seconds=request.digest_interval_seconds,
        )
        spec = build_watch_action_input(kind=WatchActionKind.BOWER.value, config=config.model_dump(mode="json"))
        try:
            action = self.watches.action_svc.add(path_id=watch.initial_path_id, action=spec)
        except (PolicyError, ConcurrentChainError, ValidationError) as exc:
            logger.warning("bower connect: signal %s created, action not added: %s", created.sheet.id, exc)
            raise ActionNotAdded(
                f"The signal {created.sheet.label!r} was created in OpenBower, but the action was not added "
                f"({exc}). Copy its webhook URL from OpenBower and add a bower action to the watch."
            ) from exc
        return BowerConnection(
            signal_id=created.sheet.id, signal_label=created.sheet.label, columns=answered, action=action
        )

    def _create_signal(self, request: BowerConnectInput, *, label: str, columns: list[DerivedColumn]) -> _CreatedSignal:
        url = request.api_url.rstrip("/") + SIGNALS_PATH
        reason = destination_block_reason(
            url,
            require_https=settings.WEBHOOK_REQUIRE_HTTPS,
            block_private_ips=settings.WEBHOOK_BLOCK_PRIVATE_IPS,
            resolve_dns=True,
        )
        if reason:
            raise BowerConnectError(f"api_url rejected: {reason}")
        body = {
            "type": CUSTOM_SIGNAL_TYPE,
            "label": label,
            "columns": [{"label": column.label, "type": column.type} for column in columns],
        }
        headers = {"Authorization": f"Bearer {request.api_key}"}
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=settings.WEBHOOK_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            logger.warning("bower connect: create failed %s", type(exc).__name__)
            raise BowerUnreachable(f"OpenBower did not answer at {request.api_url} ({type(exc).__name__})") from exc
        if response.status_code != httpx.codes.CREATED:
            raise BowerRefused(f"OpenBower refused the signal ({response.status_code}): {_detail(response)}")
        try:
            return _CreatedSignal.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise BowerAnsweredOddly("OpenBower's answer to the create was not a signal") from exc


def _scalar_fields(payload: type[BaseModel]) -> dict[str, str]:
    """Each field a payload class declares that a cell can hold, with its
    column type; lists, objects, and the plumbing fields are left out."""
    fields: dict[str, str] = {}
    for name, info in payload.model_fields.items():
        if name in _NOT_COLUMNS:
            continue
        column_type = _column_type(name, info.annotation)
        if column_type is not None:
            fields[name] = column_type
    return fields


def _column_type(name: str, annotation: Any) -> str | None:
    base = _without_none(annotation)
    if base is bool:
        return TEXT
    if base is int:
        return NUMBER
    if base is datetime:
        return TEXT
    if base is str:
        return URL if name in _URL_FIELDS else TEXT
    return None


def _without_none(annotation: Any) -> Any:
    """`X | None` -> `X`; anything else unchanged."""
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(members) == 1:
            return members[0]
    return annotation


def _label(field: str) -> str:
    """`external_url` -> `External url`."""
    words = field.replace("_", " ")
    return words[:1].upper() + words[1:]


def _detail(response: httpx.Response) -> str:
    """OpenBower's own words for a refusal, when its body carries them."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error")
        if isinstance(detail, str) and detail:
            return detail
        return str(body)
    return response.reason_phrase
