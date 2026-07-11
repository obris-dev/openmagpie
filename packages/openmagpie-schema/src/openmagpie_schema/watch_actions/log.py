"""The log kind: server-log delivery config + result. No secrets."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from openmagpie_schema.watch_enums import WatchActionKind

from ._delivery import DeliveryConfigBase
from .base import WatchActionConfigBase, WatchActionConfigSummary


class LogConfig(DeliveryConfigBase):
    """Config for a WatchAction with kind == 'log'.

    Writes the item to the server log under `prefix`. `include_fields`
    whitelists item fields (empty = all). `delivery` /
    `digest_interval_seconds` (from the base) pick instant vs batched. No
    secrets, so the dump is plain and an edit replaces wholesale.

    Digest log delivery is best-effort AT-LEAST-ONCE: a crash after the batch
    is logged but before it's marked done re-logs the line on the next flush
    (a log line carries no idempotency key to dedup on, unlike webhook). A
    duplicate log line is harmless ; don't treat the log as an exactly-once
    record."""

    CONFIG_KIND: ClassVar[str] = WatchActionKind.LOG.value

    prefix: str = "[watch]"
    include_fields: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """No secrets in a log action, so a plain dump is safe."""
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        return WatchActionConfigSummary(detail=f"log {self.prefix} ({self.delivery_label()})")

    def merge_preserving(self, prior: WatchActionConfigBase) -> LogConfig:
        """Nothing to carry forward: no secrets, the submitted config wins."""
        return self


class LogResult(BaseModel):
    """Result a log run writes: the line it emitted (kept for the audit)."""

    rendered: str = ""
