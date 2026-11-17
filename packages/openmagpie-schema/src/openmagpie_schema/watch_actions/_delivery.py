"""Shared base for the DELIVERY kinds (webhook, bower, log): cadence + digest
window length. The FILTER kind (semantic_filter) doesn't deliver, so it
doesn't carry these."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from openmagpie_schema.watch_enums import DeliveryCadence

from .base import WatchActionConfigBase


class DeliveryCadenceFields(BaseModel):
    """`delivery` is the cadence: INSTANT fires per item, DIGEST batches a
    fixed window (anchored at the first item) into one emission.
    `digest_interval_seconds` is that window's length. Shared by every
    delivery config and by any request that sets a delivery's cadence
    before its config exists (connect-bower), so both refuse alike.

    Two-layer validation: the PRESENCE rule (DIGEST requires a positive
    interval) is a pure structural invariant enforced here, so the CLI gets
    local feedback without a server round-trip. The MAGNITUDE bound (the
    min/max seconds) is settings-coupled and lives in server policy
    (`watches.policy.enforce_digest_interval`)."""

    delivery: DeliveryCadence = DeliveryCadence.INSTANT
    digest_interval_seconds: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _digest_requires_interval(self) -> DeliveryCadenceFields:
        # Asymmetric on purpose: DIGEST needs a positive interval, but INSTANT
        # with a nonzero interval is TOLERATED (the field is ignored when
        # instant), so flipping digest->instant doesn't force clearing it.
        if self.delivery == DeliveryCadence.DIGEST and self.digest_interval_seconds <= 0:
            raise ValueError("digest_interval_seconds must be > 0 when delivery is digest")
        return self

    def is_digest(self) -> bool:
        return self.delivery == DeliveryCadence.DIGEST


class DeliveryConfigBase(WatchActionConfigBase, DeliveryCadenceFields):
    """A delivery kind's config: the shared cadence plus its own fields."""

    def delivery_label(self) -> str:
        """Human cadence for the CLI summary."""
        return f"digest/{self.digest_interval_seconds}s" if self.is_digest() else "instant"
