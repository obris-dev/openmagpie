"""The registry: WHICH models the cross-boundary schema covers.

Three lists, kept separate from the build/guard logic in generate.py so the
"what" stays a flat, reviewable declaration:

- CONTRACT_MODELS: the roots emitted into the schema (nested models, the
  source-spec union, and enums come along by ref).
- EXCLUDED_MODELS: package models deliberately left out, each with a reason;
  the completeness guard fails on anything neither emitted nor listed here.
- INPUT_MODELS: the client-authored request roots; the mode-parity guard walks
  their $ref closure to verify the serialization-mode schema is also what the
  server validates.
"""

from openmagpie_schema.auth import AuthUser
from openmagpie_schema.backfill import BackfillJob, BackfillListResponse, BackfillPreview
from openmagpie_schema.configs import (
    HackerNewsCommentSourceSpec,
    HackerNewsFeedSourceSpec,
    RedditSubredditSourceSpec,
    RssSourceSpec,
)
from openmagpie_schema.engine import EngineListResponse, EngineStatus
from openmagpie_schema.feed import (
    CuratedFeedConfig,
    FeedInput,
    FeedItemListResponse,
    FeedItemWire,
    FeedListResponse,
    FeedMutationResponse,
    FeedView,
    SourceInput,
    SourceSetPayload,
    SourceSetResult,
    SourceWire,
)
from openmagpie_schema.telemetry import TelemetryState
from openmagpie_schema.watch import (
    ExtractActionInput,
    LogActionInput,
    PluginActionInput,
    SemanticFilterActionInput,
    WatchActionDeliveryListResponse,
    WatchActionDeliveryView,
    WatchActionMutationResponse,
    WatchActionRunListResponse,
    WatchActionRunView,
    WatchInput,
    WatchListResponse,
    WatchMutationResponse,
    WatchView,
    WebhookActionInput,
)
from openmagpie_schema.watch_actions import (
    ExtractConfig,
    ExtractResult,
    LogConfig,
    LogResult,
    SemanticFilterConfig,
    SemanticFilterResult,
    WebhookConfig,
    WebhookResult,
)

# Root models whose schemas (and everything they reference, pulled in as
# `$defs`) make up the schema. Grouped by domain; nested models, the source
# spec discriminated union, and the enums come along automatically by ref.
CONTRACT_MODELS = [
    # Feed read + write
    FeedView,
    FeedMutationResponse,
    FeedListResponse,
    FeedItemWire,
    FeedItemListResponse,
    FeedInput,
    CuratedFeedConfig,
    SourceInput,
    SourceWire,
    SourceSetPayload,
    SourceSetResult,
    # Watch read + write
    WatchView,
    WatchMutationResponse,
    WatchListResponse,
    WatchInput,
    WatchActionMutationResponse,
    WatchActionRunView,
    WatchActionRunListResponse,
    WatchActionDeliveryView,
    WatchActionDeliveryListResponse,
    # Backfill (re-run an action over the previous step's passes)
    BackfillPreview,
    BackfillJob,
    BackfillListResponse,
    # Per-kind action config + result (the opaque `config`/`result` blobs,
    # typed; the kind -> config map itself lives server-side in the registry)
    SemanticFilterConfig,
    SemanticFilterResult,
    ExtractConfig,
    ExtractResult,
    LogConfig,
    LogResult,
    WebhookConfig,
    WebhookResult,
    # Source specs (a discriminated union; also reached via SourceFields.spec)
    RedditSubredditSourceSpec,
    RssSourceSpec,
    HackerNewsFeedSourceSpec,
    HackerNewsCommentSourceSpec,
    # Engine + telemetry status
    EngineStatus,
    EngineListResponse,
    TelemetryState,
    # Auth identity (the shared user shape; token/device shapes stay client-specific)
    AuthUser,
]

# Models DELIBERATELY left out of the schema. The completeness guard fails on
# any package model that is neither emitted nor named here, so this list is the
# record of every conscious exclusion (add to it only with a reason). Names,
# not classes, so a removed model surfaces as a stale entry.
EXCLUDED_MODELS = frozenset(
    {
        # Abstract bases: never serialized on their own; their fields are
        # inlined into the concrete subclasses that ARE in the schema.
        # FeedConfig is field-less (CuratedFeedConfig is the concrete wire
        # shape), excluded for parity with WatchActionConfigBase.
        "FeedConfig",
        "WatchActionConfigBase",
        "EngineActionConfigBase",
        "DeliveryConfigBase",
        "SourceFields",
        "_HackerNewsSpec",
        # Kind-independent field bases for the action-node + run unions; their
        # fields inline into the per-kind members (which ARE in the contract).
        "WatchActionWireFields",
        "WatchActionInputFields",
        "WatchActionRunFields",
        # Outbound webhook body: what magpie POSTs to a third-party webhook.
        # It reaches the API only as WatchActionDeliveryView.request_payload, an
        # opaque dict, so it crosses the wire untyped and needs no schema def.
        "WebhookPayload",
        "WebhookItem",
        "WebhookSource",
        "WebhookWatchRef",
        "WebhookWindow",
    }
)

# Client-authored request roots. The schema emits everything in serialization
# mode; the mode-parity guard walks the $ref closure of these (so a nested shape
# like EngineSpec under ExtractConfig is covered too) and fails if a model's
# validation-mode and serialization-mode shapes ever diverge. For the per-kind
# configs and SourceInput that mirrors what the server actually validates the
# blob against; for the outer envelopes (which the server accepts via DRF, not
# by pydantic-validating the whole envelope) it's a self-consistency check that
# the emitted request shape matches the model's own validation shape.
INPUT_MODELS = [
    FeedInput,
    WatchInput,
    SemanticFilterActionInput,
    ExtractActionInput,
    LogActionInput,
    WebhookActionInput,
    # The plugin fallback input member (+ its open PluginConfigBlob, pulled in by ref):
    # in the parity guard for symmetry with the built-in members above, so a future
    # alias / computed field on it can't slip the validation-vs-serialization check.
    PluginActionInput,
    SourceInput,
    SourceSetPayload,
    CuratedFeedConfig,
    SemanticFilterConfig,
    ExtractConfig,
    LogConfig,
    WebhookConfig,
    RedditSubredditSourceSpec,
    RssSourceSpec,
    HackerNewsFeedSourceSpec,
    HackerNewsCommentSourceSpec,
]
