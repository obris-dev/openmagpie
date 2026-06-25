"""Per-kind WatchAction config + result contracts (the tight shapes
behind the opaque `config` / `result` blobs on WatchAction / WatchActionRun).

SHARED, zero-Django source of truth. The DB columns are opaque
(`ConfigBlob` / `ResultBlob`) ; these are the strict models the server
validates a `config` against at the API write boundary, and the strict
`result` the runner writes when it persists a run. Settings-coupled policy
(engine kind registered, threshold bounds beyond the structural gt/le,
SSRF on webhook URLs) lives server-side (`watches.policy`), not here.

`kind` is NOT a field on these configs ; it lives one level up (the
WatchAction.kind column + the write envelope's `kind`), and the server's
`watches.registry` maps `kind -> config class` to validate the blob. So
the persisted `config` is the PURE kind-specific shape, no discriminator
nested inside it. Mirrors how `feeds` keeps `Feed.kind` off `Feed.data`.

A package, one module per concern (mirrors the core-side `watches/actions/`); the
imports below are the authoritative module list. Adding / removing a kind is a
pure-Python change (no `choices=` on the column, no migration) ; import the public
names from here, not the submodules.
"""

from ._delivery import DeliveryConfigBase
from ._engine import ENRICHMENT_STATUS_KEY, EngineActionConfigBase, EngineSpec, ExternalContentStatus
from .base import WatchActionConfigBase, WatchActionConfigSummary
from .extract import (
    EXTRACT_FIELD_NAME_KEY,
    EXTRACT_FIELDS_KEY,
    EXTRACT_STATUS_KEY,
    EXTRACTED_KEY,
    ExtractConfig,
    ExtractField,
    ExtractResult,
    ExtractStatus,
)
from .log import LogConfig, LogResult
from .semantic_filter import SemanticFilterConfig, SemanticFilterResult
from .webhook import (
    WebhookConfig,
    WebhookItem,
    WebhookPayload,
    WebhookResult,
    WebhookSource,
    WebhookWatchRef,
    WebhookWindow,
)

__all__ = [
    "ENRICHMENT_STATUS_KEY",
    "EXTRACTED_KEY",
    "EXTRACT_FIELDS_KEY",
    "EXTRACT_FIELD_NAME_KEY",
    "EXTRACT_STATUS_KEY",
    "DeliveryConfigBase",
    "EngineActionConfigBase",
    "EngineSpec",
    "ExternalContentStatus",
    "ExtractConfig",
    "ExtractField",
    "ExtractResult",
    "ExtractStatus",
    "LogConfig",
    "LogResult",
    "SemanticFilterConfig",
    "SemanticFilterResult",
    "WatchActionConfigBase",
    "WatchActionConfigSummary",
    "WebhookConfig",
    "WebhookItem",
    "WebhookPayload",
    "WebhookResult",
    "WebhookSource",
    "WebhookWatchRef",
    "WebhookWindow",
]
