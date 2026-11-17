"""The bower kind: delivery to an OpenBower custom signal's webhook URL.

An HTTP delivery like the webhook, with OpenBower's push body instead of the
generic one: each item becomes a row whose cells are the item fields its
`fields` map names. The URL carries the signal's token, so it is the one
secret, masked and restored as the webhook's is.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from openmagpie_schema.watch_enums import WatchActionKind

from ._delivery import DeliveryConfigBase
from ._secrets import looks_redacted_url, redact_url
from ._url import check_delivery_url, restore_masked_url
from .base import WatchActionConfigBase, WatchActionConfigSummary

# OpenBower takes at most this many rows in one push; a delivery never sends
# more (a digest slice is capped below it, checked where the body is built).
BOWER_MAX_ROWS_PER_PUSH = 1000
# Its field keys and Magpie's item field names alike are short identifiers.
BOWER_FIELD_NAME_MAX_LENGTH = 64


class BowerConfig(DeliveryConfigBase):
    """Config for a WatchAction with kind == 'bower'.

    POSTs items to `url`, a custom signal's webhook URL, as rows. `fields`
    maps each of the signal's field keys to the item field that fills it
    (`{"subreddit": "subreddit"}`); an item lacking a field, or holding a list
    or object there, leaves that cell out. `delivery` /
    `digest_interval_seconds` (from the base) pick instant vs batched.

    Secret-bearing: the URL path holds the signal's token, so `redacted_dump`
    masks it and `merge_preserving` carries it forward when an edit leaves it
    masked."""

    CONFIG_KIND: ClassVar[str] = WatchActionKind.BOWER.value

    url: str
    fields: dict[str, str] = Field(min_length=1)

    model_config = {"extra": "ignore"}

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return check_delivery_url(v)

    @field_validator("fields")
    @classmethod
    def _validate_fields(cls, v: dict[str, str]) -> dict[str, str]:
        for key, item_field in v.items():
            if not key or not item_field:
                raise ValueError("fields maps a field key to an item field name; neither may be blank")
            if len(key) > BOWER_FIELD_NAME_MAX_LENGTH or len(item_field) > BOWER_FIELD_NAME_MAX_LENGTH:
                raise ValueError(f"field names are at most {BOWER_FIELD_NAME_MAX_LENGTH} characters")
        return v

    def redacted_dump(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["url"] = redact_url(self.url)
        return data

    def summary(self) -> WatchActionConfigSummary:
        host = urlsplit(self.url).hostname or "?"
        return WatchActionConfigSummary(detail=f"OpenBower {host} ({self.delivery_label()})")

    def merge_preserving(self, prior: WatchActionConfigBase) -> BowerConfig:
        """Restore the URL the operator left masked from `prior` (an exact
        match to the masked form shown for it)."""
        prior_url = prior.url if isinstance(prior, BowerConfig) else None
        return self.model_copy(update={"url": restore_masked_url(self.url, prior_url)})

    def has_masked_secret(self) -> bool:
        return looks_redacted_url(self.url)


class BowerResult(BaseModel):
    """Result a bower run writes to WatchActionRun.result: the HTTP status of
    the push that succeeded and how many rows OpenBower accepted (fewer than
    sent when it already held some, by external_id)."""

    http_status: int
    accepted: int | None = None


# ── Outbound payload contract (OpenBower's push envelope) ──────────────────


class BowerRow(BaseModel):
    """One row: the item's stable key as the row's `external_id` (a resend
    lands once) and its mapped fields as `cells`."""

    external_id: str
    cells: dict[str, str] = Field(default_factory=dict)


class BowerPush(BaseModel):
    """The body POSTed to a custom signal's webhook URL. `request_id` is the
    same for a resent batch, so OpenBower treats the resend as the request it
    already took."""

    request_id: str
    rows: list[BowerRow] = Field(default_factory=list)
