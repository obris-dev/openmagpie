"""`POST /v1/watches/<id>/connect-bower`: create an OpenBower custom signal for
a watch and add the `bower` action that delivers to it."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..watch_actions._url import check_delivery_url
from ..watch_enums import DeliveryCadence
from ._nodes import WatchActionWire


class BowerConnectInput(BaseModel):
    """`api_key` is an OpenBower API key, used for the one create and never
    stored. `label` names the signal (empty = the watch's name). The cadence
    fields become the added action's."""

    api_url: str
    api_key: str = Field(min_length=1)
    label: str = ""
    delivery: DeliveryCadence = DeliveryCadence.INSTANT
    digest_interval_seconds: int = Field(default=0, ge=0)

    @field_validator("api_url")
    @classmethod
    def _validate_api_url(cls, v: str) -> str:
        return check_delivery_url(v)


class BowerConnectColumn(BaseModel):
    """One column of the signal: its label, type, and the item field that
    fills it, and its OpenBower key once created (empty on a dry run)."""

    key: str = ""
    label: str
    type: str
    field: str


class BowerConnectResponse(BaseModel):
    """The connect's answer (real and `?dry_run=true`). A dry run calls
    OpenBower not at all: it names the signal and the columns a connect would
    create, with no `signal_id` and no `action`. A real one carries the
    created signal and the action added to the watch (its URL masked)."""

    dry_run: bool
    signal_id: str | None = None
    signal_label: str
    columns: list[BowerConnectColumn] = Field(default_factory=list)
    action: WatchActionWire | None = None
