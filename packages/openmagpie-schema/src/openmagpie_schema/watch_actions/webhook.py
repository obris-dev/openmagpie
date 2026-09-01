"""The webhook kind: HTTP delivery config + result (POST | PUT | PATCH).

The first SECRET-BEARING kind: the URL path/query and header values can
carry auth tokens, so this is the module that exercises the
`redacted_dump` / `merge_preserving` machinery (see `_secrets`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from openmagpie_schema.watch_enums import DeliveryCadence, WatchActionKind, WebhookMethod

from ._delivery import DeliveryConfigBase
from ._secrets import REDACTED, looks_redacted_url, redact_url
from .base import WatchActionConfigBase, WatchActionConfigSummary


class WebhookConfig(DeliveryConfigBase):
    """Config for a WatchAction with kind == 'webhook'.

    Sends the item to `url` via `method` (POST | PUT | PATCH) with `headers`
    (sent verbatim, so they carry any auth token). `include_fields` whitelists
    which item fields are sent (empty = all). `delivery` /
    `digest_interval_seconds` (from the base) pick instant vs batched delivery.

    Secret-bearing: `url` path/query and every header VALUE are masked by
    `redacted_dump` and carried forward by `merge_preserving` when the
    operator leaves them masked on an edit."""

    CONFIG_KIND: ClassVar[str] = WatchActionKind.WEBHOOK.value

    url: str
    method: WebhookMethod = WebhookMethod.POST
    headers: dict[str, str] = Field(default_factory=dict)
    include_fields: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        """Structural check: an http(s) URL with a host AND an in-range port.
        The SSRF gate (private-IP / require-https) is settings-coupled, so it
        lives in `watches.policy` (write) + the impl (send), not here. A
        redacted `scheme://host/***` still passes (host + scheme present), so
        an edit that leaves the URL masked validates and `merge_preserving`
        restores the real one.

        The port is validated HERE so a bad one can never persist: `.port` is
        a lazy property that raises only on access, so a URL like
        `https://h:99999/p` would otherwise pass and then crash every read
        (redacted_dump) and the send-time resolve."""
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be an http(s) URL with a host")
        try:
            parts.port  # noqa: B018 - property access validates the port range
        except ValueError:
            raise ValueError("url has an out-of-range port") from None
        return v

    def redacted_dump(self) -> dict[str, Any]:
        """Mask both secret carriers: the URL path/query and every header
        value. The read path (CLI preview, GET) never sees a live token."""
        data = self.model_dump(mode="json")
        data["url"] = redact_url(self.url)
        data["headers"] = dict.fromkeys(self.headers, REDACTED)
        return data

    def summary(self) -> WatchActionConfigSummary:
        host = urlsplit(self.url).hostname or "?"
        return WatchActionConfigSummary(detail=f"{self.method.value} {host} ({self.delivery_label()})")

    def merge_preserving(self, prior: WatchActionConfigBase) -> WebhookConfig:
        """Restore secrets the operator left masked from `prior`.

        URL restore is by EXACT match, not shape: the URL is restored only if
        the operator submitted back precisely the masked form we showed for
        THIS action's prior (`redact_url(prior.url)`). A real URL that merely
        ends in `/***` therefore survives instead of being silently swapped
        for the prior. Headers restore on the exact `***` sentinel; a header
        left masked with no prior value to restore raises (re-enter it)."""
        prior_webhook = prior if isinstance(prior, WebhookConfig) else None
        # Restore only on an EXACT match to the masked form we showed for the
        # prior (not shape) ; a real URL ending in /*** is kept.
        url = prior_webhook.url if prior_webhook is not None and self.url == redact_url(prior_webhook.url) else self.url
        headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if value != REDACTED:
                headers[name] = value
            elif prior_webhook is not None and name in prior_webhook.headers:
                headers[name] = prior_webhook.headers[name]
            else:
                raise ValueError(f"header {name!r} is masked but there is no prior value to restore; re-enter it")
        return self.model_copy(update={"url": url, "headers": headers})

    def has_masked_secret(self) -> bool:
        """Conservative: does this config look like it still carries a masked
        URL or header? Shape-based (looks_redacted_url), used only by the
        chain-replace guard to refuse an ambiguous pairing ; a false positive
        there is a safe refusal, never a silent secret swap."""
        return looks_redacted_url(self.url) or any(v == REDACTED for v in self.headers.values())


class WebhookResult(BaseModel):
    """Result a webhook run writes to WatchActionRun.result: the HTTP status
    of the call that SUCCEEDED (a 2xx ; a non-2xx raises and the run is
    FAILED instead)."""

    http_status: int


# ── Outbound payload contract (the body sent to the receiver) ──────────────


class WebhookWatchRef(BaseModel):
    """Identifies the watch a delivery came from, so a receiver can label the
    source listener (the fix for the "(unnamed listener)" digest)."""

    id: str
    name: str


class WebhookWindow(BaseModel):
    """The time window a digest batch covers. Null on instant delivery (a
    single item, no window)."""

    since: datetime
    until: datetime


class WebhookSource(BaseModel):
    """Which feed source an item came from: the source's display `label`, its
    connector `kind`, and the optional operator-supplied `pattern_id` tag
    (yield attribution: which listening pattern produced this item).
    `pattern_id` is None when the source carries no pattern tag (e.g. a
    non-listening source) ; receivers map it to their pattern-attribution
    field when present."""

    label: str
    kind: str
    pattern_id: str | None = None


class WebhookItem(BaseModel):
    """One delivered item: its stable `key` (source:external_id), the
    originating `source`, and the field-filtered `item` body. (Upstream run
    results, e.g. the filter score, are a separate opt-in run-history
    enrichment ; not carried here yet.)"""

    key: str
    source: WebhookSource
    item: dict[str, Any] = Field(default_factory=dict)


class WebhookPayload(BaseModel):
    """The HTTP body delivered for BOTH cadences. Instant is a one-item batch
    with `window` null ; digest carries N items plus the window bounds. The
    shape is self-describing: a receiver renders it (which watch, which window,
    per-item source) without out-of-band knowledge."""

    watch: WebhookWatchRef
    action_id: str
    delivery: DeliveryCadence
    window: WebhookWindow | None = None
    items: list[WebhookItem] = Field(default_factory=list)
