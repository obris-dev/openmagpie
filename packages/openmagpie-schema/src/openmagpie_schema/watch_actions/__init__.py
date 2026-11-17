"""Per-kind WatchAction config + result contracts (the tight shapes behind the
`config` / `result` JSON columns on WatchAction / WatchActionRun).

SHARED, zero-Django source of truth. The DB columns store the PURE kind-specific
dict (no discriminator nested) ; these are the strict models the server
validates a `config` against at the API write boundary, and the strict
`result` the runner writes when it persists a run. On the wire they are carried
as discriminated unions keyed by `kind` (`watch/_nodes.py` for the action config,
`watch/_runs.py` for the run result). Settings-coupled policy
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
from .bower import BOWER_MAX_ROWS_PER_PUSH, BowerConfig, BowerPush, BowerResult, BowerRow
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
    "BOWER_MAX_ROWS_PER_PUSH",
    "ENRICHMENT_STATUS_KEY",
    "EXTRACTED_KEY",
    "EXTRACT_FIELDS_KEY",
    "EXTRACT_FIELD_NAME_KEY",
    "EXTRACT_STATUS_KEY",
    "BowerConfig",
    "BowerPush",
    "BowerResult",
    "BowerRow",
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
