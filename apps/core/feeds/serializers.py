"""Feeds API wire shapes.

Input: `FeedCreateSerializer` (the HTTP request-validation boundary,
delegating the `data` blob to the Pydantic registry). Output: plain
builders (`feed_wire` / `feed_view` / `feed_mutation`) populating the
shared `openmagpie_schema.feed` models, so the server is their authority
and the CLI imports the same classes.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from common.pydantic_errors import pydantic_errors_to_drf
from feeds.models import Feed, FeedItem, Source
from feeds.models.feed import MIN_POLL_INTERVAL_SECONDS
from feeds.policy import PolicyError
from feeds.registry import get_config_class, load_config, validate_config
from feeds.services.sources import SourceService
from openmagpie_schema.feed import (
    FeedConfigSummary,
    FeedItemWire,
    FeedMutationResponse,
    FeedView,
    FeedWire,
    SourceInput,
    SourceWire,
)

# The single adapter shared across the request-validation seams
# (serializer + sub-router PUT). One definition, one shape.
SOURCE_INPUT_LIST_ADAPTER = TypeAdapter(list[SourceInput])

logger = logging.getLogger("feeds")


# ── Input ──────────────────────────────────────────────────────────────


class FeedCreateSerializer(serializers.Serializer):
    """Envelope for POST /v1/feeds. The kind-specific config arrives as
    `data` (validated via the Pydantic registry). `sources` is a
    top-level optional list of starter Source rows for the curated
    feed; PUT silently ignores it (use the sources sub-router to
    mutate after create)."""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    kind = serializers.CharField(max_length=32, default="curated")
    poll_interval_seconds = serializers.IntegerField(min_value=MIN_POLL_INTERVAL_SECONDS, default=300)
    # False creates/leaves the feed paused: the poll pass skips inactive feeds. Default
    # True so an existing client (or a create that omits it) keeps today's behavior.
    is_active = serializers.BooleanField(default=True)
    data = serializers.DictField(child=serializers.JSONField())
    sources = serializers.ListField(child=serializers.DictField(), required=False, default=list)

    def validate_kind(self, value: str) -> str:
        try:
            get_config_class(value)
        except KeyError:
            raise serializers.ValidationError(f"unknown feed kind {value!r}") from None
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # validate_config = shape (Pydantic) + policy (retention bounds).
        # Each failure maps to its own 400 shape.
        try:
            validated = validate_config(attrs["kind"], attrs["data"])
        except PydanticValidationError as exc:
            raise serializers.ValidationError({"data": pydantic_errors_to_drf(exc)}) from exc
        except PolicyError as exc:
            raise serializers.ValidationError({"data": [str(exc)]}) from exc
        attrs["data"] = validated.model_dump(mode="json")

        raw_sources = attrs.get("sources") or []
        if raw_sources:
            try:
                attrs["sources"] = SOURCE_INPUT_LIST_ADAPTER.validate_python(raw_sources)
            except PydanticValidationError as exc:
                raise serializers.ValidationError({"sources": pydantic_errors_to_drf(exc)}) from exc
        else:
            attrs["sources"] = []
        return attrs


class FeedSetActiveSerializer(serializers.Serializer):
    """PATCH /v1/feeds/<id> body: the pause/resume toggle. Just the one bit, so the
    config / sources are untouched (a full PUT would re-validate + merge)."""

    is_active = serializers.BooleanField()


# ── Output ─────────────────────────────────────────────────────────────

_EMPTY_SUMMARY = FeedConfigSummary()


def _redacted_data(feed: Feed) -> dict[str, Any]:
    """`feed.data` validated through the kind's typed config and redacted.
    Per-row fail-safe: a corrupt row degrades to a sentinel, never 500s a
    `many` list."""
    try:
        return load_config(feed).redacted_dump()
    except Exception:
        logger.exception("feed %s data failed redaction (kind=%s); returning sentinel", feed.id, feed.kind)
        return {"error": "config_unreadable"}


def _feed_summary(feed: Feed) -> FeedConfigSummary:
    """Display projection from the typed config; same per-row fail-safe."""
    try:
        return load_config(feed).summary()
    except Exception:
        return _EMPTY_SUMMARY


def feed_item_wire(item: FeedItem) -> FeedItemWire:
    """One FeedItem's wire row. Used inline by `feed_view`'s recent-item list and
    by the item audit views (`/v1/feeds/<id>/items`, `/v1/feed-items/<id>`).

    `model_validate` (not the kwarg constructor) so the `data` dump is parsed
    into the typed `FeedItemData` union at this boundary; a raw dict is not
    statically one of the payload models."""
    return FeedItemWire.model_validate(
        {
            "id": str(item.id),
            "source_kind": str(item.source_kind),
            "source_label": str(item.source_label),
            "external_id": str(item.external_id),
            "occurred_at": item.occurred_at,
            "data": item.data or {},
        }
    )


def source_wire(source: Source) -> SourceWire:
    """Single source for a Source row's wire envelope."""
    return SourceWire(
        id=str(source.id),
        spec=source.spec,
        meta=source.meta or {},
        field_map=source.field_map or {},
        last_event_at=source.last_event_at,
        created_at=source.created_at,
    )


def feed_wire(feed: Feed) -> FeedWire:
    """Single source for a Feed's kind-independent wire envelope. Tolerates
    an unsaved instance (dry-run): created/poll timestamps None, id empty."""
    return FeedWire(
        id=str(feed.id),
        name=feed.name,
        kind=str(feed.kind),
        is_active=feed.is_active,
        poll_interval_seconds=feed.poll_interval_seconds,
        last_polled_at=feed.last_polled_at,
        next_poll_at=feed.next_poll_at,
        user_id=str(feed.user_id),
        data=_redacted_data(feed),
        created_at=feed.created_at,
    )


def _feed_sources(feed: Feed) -> list[Source]:
    """Service-mediated source-row read for the wire builders. Unsaved
    feeds (create dry-run) return an empty list ; the rows don't exist
    yet, no need to hit the DB."""
    if not feed.pk or not feed.id:
        return []
    return SourceService(account_id=str(feed.account_id)).list(feed)


def feed_view(feed: Feed) -> FeedView:
    """GET-detail (CONFIG) response: envelope + summary + the feed's
    currently-attached Source rows. The item log is NOT here ; it has its own
    paginated route (`GET /v1/feeds/<id>/items`)."""
    sources_qs = _feed_sources(feed)
    return FeedView(
        **feed_wire(feed).model_dump(),
        summary=_feed_summary(feed),
        sources=[source_wire(s) for s in sources_qs],
        source_count=len(sources_qs),
    )


def feed_mutation(feed: Feed, *, dry_run: bool) -> FeedMutationResponse:
    """Create / edit response: envelope + summary + sources + dry_run.

    Sources are enriched here (not only on FeedView) so the CLI's
    confirm-preview after `feed create -f ...` shows the operator
    exactly which sources were attached."""
    sources_qs = _feed_sources(feed)
    return FeedMutationResponse(
        **feed_wire(feed).model_dump(),
        summary=_feed_summary(feed),
        sources=[source_wire(s) for s in sources_qs],
        source_count=len(sources_qs),
        dry_run=dry_run,
    )
