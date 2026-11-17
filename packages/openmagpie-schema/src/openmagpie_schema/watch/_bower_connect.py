"""`POST /v1/watches/<id>/connect-bower`: connect a watch to an OpenBower custom
signal, a new one or an existing one, by adding the `bower` action that
delivers to it."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Discriminator, Field, Tag, field_validator, model_validator

from ..watch_actions._delivery import DeliveryCadenceFields
from ..watch_actions._url import check_delivery_url
from ._nodes import WatchActionWire


class _BowerConnectCommon(DeliveryCadenceFields):
    """What both connects take. `fields` names the item fields to send, in
    column order (empty = every field the watch's sources produce). The
    cadence fields become the added action's, and `rank` its place in the
    chain (None appends it)."""

    fields: list[str] = Field(default_factory=list)
    rank: int | None = Field(default=None, ge=0)

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, v: list[str]) -> list[str]:
        names = [name.strip() for name in v]
        if any(not name for name in names):
            raise ValueError("a field name is blank")
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"fields repeats {', '.join(repeated)}")
        return names


class BowerConnectExisting(_BowerConnectCommon):
    """Connect an existing signal by its webhook URL as OpenBower shows it:
    its columns are read from the URL and matched to the item fields."""

    webhook_url: str

    @model_validator(mode="before")
    @classmethod
    def _no_create_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and any(data.get(name) for name in _CREATE_ONLY):
            raise ValueError("webhook_url connects an existing signal; leave out api_url, api_key and label")
        return data

    @field_validator("webhook_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return check_delivery_url(v)


class BowerConnectNew(_BowerConnectCommon):
    """Create a new signal through OpenBower's API at `api_url`, with an API
    key used for the one create and never stored, named `label` (empty =
    the watch's name)."""

    api_url: str
    api_key: str = Field(min_length=1)
    label: str = ""

    @field_validator("api_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return check_delivery_url(v)


# The fields only a new signal takes, refused beside a webhook_url.
_CREATE_ONLY = ("api_url", "api_key", "label")
_ROUTE_MISSING = "give webhook_url to connect an existing signal, or api_url and api_key to create one"


def _route(value: Any) -> str | None:
    """Which connect a body asks for: a webhook_url is an existing signal,
    an api_url or api_key a new one."""
    if isinstance(value, BaseModel):
        return "existing" if isinstance(value, BowerConnectExisting) else "new"
    if not isinstance(value, dict):
        return None
    if value.get("webhook_url"):
        return "existing"
    if value.get("api_url") or value.get("api_key"):
        return "new"
    return None


# The connect's request: one shape per route, picked by which URL it carries.
BowerConnectInput = Annotated[
    Annotated[BowerConnectExisting, Tag("existing")] | Annotated[BowerConnectNew, Tag("new")],
    Discriminator(_route, custom_error_type="connect_route", custom_error_message=_ROUTE_MISSING),
]


class BowerConnectColumn(BaseModel):
    """One column of the signal: its label, type, the item field that fills
    it (empty for an existing signal's column no field matches, left blank),
    and its OpenBower key (empty on a dry run that creates)."""

    key: str = ""
    label: str
    type: str
    field: str


class BowerConnectResponse(BaseModel):
    """The connect's answer (real and `?dry_run=true`). A dry run adds
    nothing: one that creates names the signal and the columns it would
    make, calling OpenBower not at all; one for an existing signal reads its
    columns and shows how the fields match. A real one carries the action
    added to the watch (its URL masked). `signal_label` is empty for an
    existing signal (its webhook URL does not name it); `unmatched_fields`
    are the chosen item fields no column takes, and `left_out_fields` the
    ones `fields` left out."""

    dry_run: bool
    signal_id: str | None = None
    signal_label: str
    columns: list[BowerConnectColumn] = Field(default_factory=list)
    unmatched_fields: list[str] = Field(default_factory=list)
    left_out_fields: list[str] = Field(default_factory=list)
    action: WatchActionWire | None = None
