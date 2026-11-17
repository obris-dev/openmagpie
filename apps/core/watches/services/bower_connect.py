"""Connect a watch to OpenBower: add the `bower` action that delivers to a
custom signal, either a new one created with columns for the watch's item
fields, or an existing one whose webhook URL is given (its columns read from
the URL and matched to the item fields by label).

The OpenBower API key is used for the one create and never stored, logged,
or answered with: the action keeps only the signal's webhook URL, which
OpenBower can rotate on its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from functools import cached_property

import httpx
from pydantic import BaseModel, ValidationError

from feeds.services import FeedService, SourceService
from openmagpie_schema.watch import (
    BowerConnectColumn,
    BowerConnectInput,
    BowerConnectNew,
    build_watch_action_input,
)
from openmagpie_schema.watch_actions import BowerConfig
from openmagpie_schema.watch_enums import WatchActionKind
from sources.payload_registry import class_for_source
from sources.payloads import SourcePayload
from watches.models import Watch, WatchAction
from watches.policy import PolicyError, enforce_digest_interval

from . import _bower_http
from ._bower_errors import (
    ActionNotAdded,
    BowerAnsweredOddly,
    BowerConnectError,
    BowerRefused,
    BowerUnreachable,
    NoItemFields,
    is_outage,
)
from ._bower_fields import LEADING_FIELDS, ChosenColumns, DerivedColumn, choose, field_label, scalar_fields
from ._bower_resolve import ResolvedSignal, existing, planned
from .watches import WatchService
from .watches._actions import ConcurrentChainError

logger = logging.getLogger("watches")

SIGNALS_PATH = "/v1/signals"
# The most of OpenBower's refusal a connect relays to its caller.
RELAYED_DETAIL_MAX_LENGTH = 300
CUSTOM_SIGNAL_TYPE = "custom"


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
class BowerPlan:
    """What a connect would do: the signal (no id or keys yet when it would
    be created), its columns, the chosen item fields no column takes, and
    the ones the request left out."""

    signal_id: str | None
    signal_label: str
    columns: list[BowerConnectColumn]
    unmatched_fields: list[str]
    left_out_fields: list[str]


@dataclass(frozen=True)
class BowerConnection:
    signal_id: str
    signal_label: str
    columns: list[BowerConnectColumn]
    unmatched_fields: list[str]
    left_out_fields: list[str]
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
        feed_ids = self.watches.feed_ids(watch)
        # A deleted feed's subscription can outlive it (the trigger skips
        # it too), so only the feeds still there give fields.
        feeds = self.feeds.get_many(feed_ids)
        for feed in feeds.values():
            for source in self.sources.list(feed):
                if source.kind not in kinds:
                    kinds.append(source.kind)
        if not kinds:
            raise NoItemFields("the watch's feeds have no sources, so there are no item fields to send")
        fields: dict[str, str] = scalar_fields(SourcePayload)
        for kind in kinds:
            payload = class_for_source(kind)
            if payload is not None:
                for name, column_type in scalar_fields(payload).items():
                    fields.setdefault(name, column_type)
        names = [name for name in LEADING_FIELDS if name in fields]
        names += [name for name in fields if name not in names]
        return [DerivedColumn(field=name, label=field_label(name), type=fields[name]) for name in names]

    def chosen_for(self, watch: Watch, request: BowerConnectInput) -> ChosenColumns:
        """The columns the request sends: the fields it names, or every one.
        Also where a preview and a connect refuse the cadence, before any
        call to OpenBower, so a bad one never leaves a created signal."""
        try:
            enforce_digest_interval(request)
        except PolicyError as exc:
            raise BowerConnectError(str(exc)) from exc
        return choose(self.columns_for(watch), request.fields)

    def preview(self, watch: Watch, request: BowerConnectInput) -> BowerPlan:
        """What a connect would do, adding nothing. A create calls OpenBower
        not at all; an existing signal's columns are read from its URL."""
        chosen = self.chosen_for(watch, request)
        if isinstance(request, BowerConnectNew):
            signal = planned(request.label or watch.name, chosen)
        else:
            signal = existing(request, chosen)
        return BowerPlan(
            signal_id=signal.signal_id,
            signal_label=signal.label,
            columns=signal.columns,
            unmatched_fields=signal.unmatched_fields,
            left_out_fields=chosen.left_out,
        )

    def connect(self, watch: Watch, request: BowerConnectInput) -> BowerConnection:
        chosen = self.chosen_for(watch, request)
        if isinstance(request, BowerConnectNew):
            signal = self._created(request, label=request.label or watch.name, chosen=chosen)
        else:
            signal = existing(request, chosen)
        action = self._add(watch, request, signal)
        return BowerConnection(
            signal_id=signal.signal_id or "",
            signal_label=signal.label,
            columns=signal.columns,
            unmatched_fields=signal.unmatched_fields,
            left_out_fields=chosen.left_out,
            action=action,
        )

    def _created(self, request: BowerConnectNew, *, label: str, chosen: ChosenColumns) -> ResolvedSignal:
        """A new signal with a column per chosen field, its keys read back."""
        created = self._create_signal(request, label=label, columns=chosen.columns)
        signal = ResolvedSignal(
            signal_id=created.sheet.id,
            label=created.sheet.label,
            url=created.webhook_url,
            columns=[],
            column_map={},
            unmatched_fields=[],
            created=True,
        )
        try:
            columns, column_map = _created_columns(created, chosen.columns)
        except BowerAnsweredOddly as exc:
            raise BowerAnsweredOddly(signal.not_added(str(exc))) from exc
        return replace(signal, columns=columns, column_map=column_map)

    def _add(self, watch: Watch, request: BowerConnectInput, signal: ResolvedSignal) -> WatchAction:
        """The one step both routes share: the bower action, delivering to it."""
        try:
            return self._add_action(watch, request, url=signal.url, column_map=signal.column_map)
        except (PolicyError, ConcurrentChainError) as exc:
            logger.warning("bower connect: signal %s, action not added: %s", signal.signal_id, exc)
            raise ActionNotAdded(signal.not_added(str(exc))) from exc
        except ValidationError as exc:
            # The messages only: the error's own text quotes the config, URL included.
            reasons = "; ".join(error["msg"] for error in exc.errors())
            logger.warning("bower connect: signal %s, action not added: %s", signal.signal_id, reasons)
            raise ActionNotAdded(signal.not_added(reasons)) from exc

    def _add_action(
        self, watch: Watch, request: BowerConnectInput, *, url: str, column_map: dict[str, str]
    ) -> WatchAction:
        config = BowerConfig(
            url=url,
            columns=column_map,
            delivery=request.delivery,
            digest_interval_seconds=request.digest_interval_seconds,
        )
        dumped = config.model_dump(mode="json")
        spec = build_watch_action_input(kind=WatchActionKind.BOWER.value, config=dumped)
        return self.watches.action_svc.add(path_id=watch.initial_path_id, action=spec, rank=request.rank)

    def _create_signal(self, request: BowerConnectNew, *, label: str, columns: list[DerivedColumn]) -> _CreatedSignal:
        url = request.api_url.rstrip("/") + SIGNALS_PATH
        _bower_http.check(url, field="api_url")
        body = {
            "type": CUSTOM_SIGNAL_TYPE,
            "label": label,
            "columns": [{"label": column.label, "type": column.type} for column in columns],
        }
        headers = {"Authorization": f"Bearer {request.api_key}"}
        try:
            response = _bower_http.post(url, field="api_url", json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("bower connect: create failed %s", type(exc).__name__)
            raise BowerUnreachable(f"OpenBower did not answer at {request.api_url} ({type(exc).__name__})") from exc
        status = response.status_code
        if is_outage(status):
            raise BowerUnreachable(f"OpenBower could not create the signal now ({status}); try again later")
        if status != httpx.codes.CREATED:
            raise BowerRefused(f"OpenBower refused the signal ({status}): {_detail(response)}")
        try:
            return _CreatedSignal.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise BowerAnsweredOddly("OpenBower's answer to the create was not a signal") from exc


def _created_columns(
    created: _CreatedSignal, derived: list[DerivedColumn]
) -> tuple[list[BowerConnectColumn], dict[str, str]]:
    """The created signal's columns, keyed back to the item fields they were
    made for (by label), and the action's column key -> field map."""
    by_label = {column.label: column for column in created.sheet.columns}
    column_map: dict[str, str] = {}
    answered: list[BowerConnectColumn] = []
    for column in derived:
        made = by_label.get(column.label)
        if made is None:
            raise BowerAnsweredOddly(f"OpenBower created the signal without a {column.label!r} column")
        column_map[made.key] = column.field
        answered.append(BowerConnectColumn(key=made.key, label=made.label, type=made.type, field=column.field))
    return answered, column_map


def _detail(response: httpx.Response) -> str:
    """OpenBower's own words for a refusal: its `detail` or `error` string,
    cut to a bound. Nothing else of the body is relayed, so the answer never
    echoes whatever an address that is not OpenBower sent back."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    detail = body.get("detail") or body.get("error") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail[:RELAYED_DETAIL_MAX_LENGTH]
    return response.reason_phrase
