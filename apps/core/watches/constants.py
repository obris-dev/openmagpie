"""Watch-domain discriminators (re-exported from the shared schema pkg).

The enums live in `openmagpie_schema.watch_enums` so the server AND the
magpie CLI type against the same source of truth. This module is the
stable in-core import path (`from watches.constants import ...`).

StrEnums, NOT Django `TextChoices` ; `choices=` stays OFF the DB columns
(a new value would otherwise force a migration). The column is a bare
CharField storing the string value ; these enums are the Python-side
source of truth for validation + branching.
"""

from openmagpie_schema.watch_enums import (
    DeliveryCadence,
    WatchActionBackfillState,
    WatchActionDeliveryState,
    WatchActionKind,
    WatchActionRunState,
    WebhookMethod,
)

__all__ = [
    "DeliveryCadence",
    "WatchActionBackfillState",
    "WatchActionDeliveryState",
    "WatchActionKind",
    "WatchActionRunState",
    "WebhookMethod",
]
