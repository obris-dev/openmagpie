"""Watches API shapes.

Input: `WatchCreateSerializer` (the HTTP request-validation boundary,
delegating each action's `config` blob to the watches registry / Pydantic
union). Output: plain builders (`watch_wire` / `watch_view` /
`watch_mutation` / `watch_action_wire`) populating the shared
`openmagpie_schema.watch` models, so the server is their authority and
the CLI imports the same classes.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from common.pydantic_errors import pydantic_errors_to_drf
from feeds.models import Feed, FeedItem
from feeds.services import FeedService
from openmagpie_schema.watch import (
    RunFeed,
    RunFeedItem,
    WatchActionDeliveryView,
    WatchActionDeliveryWire,
    WatchActionInput,
    WatchActionMutationResponse,
    WatchActionRunView,
    WatchActionRunWire,
    WatchActionWire,
    WatchMutationResponse,
    WatchView,
    WatchWire,
)
from openmagpie_schema.watch_actions import WatchActionConfigSummary
from openmagpie_schema.watch_enums import WatchActionRunState
from watches.models import Watch, WatchAction, WatchActionDelivery, WatchActionRun
from watches.policy import PolicyError
from watches.registry import KNOWN_KINDS, load_config, validate_config

logger = logging.getLogger("watches")

_EMPTY_SUMMARY = WatchActionConfigSummary()


# ── Input ──────────────────────────────────────────────────────────────


class WatchCreateSerializer(serializers.Serializer):
    """Envelope for POST/PUT /v1/watches. `feed_ids` is the subscription
    set; `actions` is the initial path's ordered chain. Each action is
    `{kind, config}` (kind adjacent to its blob, k8s-style) ; `config` is
    validated per `kind` via the watches registry.

    Mirrors `FeedCreateSerializer`: the kind-specific shapes are NOT
    re-declared as DRF fields ; the registry validates them and pydantic
    errors map to DRF's nested 400 at the right path."""

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    is_active = serializers.BooleanField(default=True)
    feed_ids = serializers.ListField(child=serializers.CharField(max_length=26), default=list)
    actions = serializers.ListField(child=serializers.DictField(), default=list)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        self._validate_feed_ids(attrs.get("feed_ids") or [])
        validated_actions: list[WatchActionInput] = []
        errors: dict[str, Any] = {}
        for i, raw in enumerate(attrs.get("actions") or []):
            kind = raw.get("kind") if isinstance(raw, dict) else None
            config = raw.get("config") if isinstance(raw, dict) else None
            if kind not in KNOWN_KINDS:
                errors[str(i)] = {"kind": [f"unknown action kind {kind!r}; known: {sorted(KNOWN_KINDS)}"]}
                continue
            if not isinstance(config, dict):
                errors[str(i)] = {"config": ["this field is required and must be an object"]}
                continue
            try:
                # shape + policy ; the persisted blob is the normalized dump.
                typed = validate_config(kind, config)
            except PydanticValidationError as exc:
                errors[str(i)] = {"config": pydantic_errors_to_drf(exc)}
                continue
            except PolicyError as exc:
                errors[str(i)] = {"config": [str(exc)]}
                continue
            action_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
            validated_actions.append(WatchActionInput(id=action_id, kind=kind, config=typed.model_dump(mode="json")))
        if errors:
            raise serializers.ValidationError({"actions": errors})
        attrs["actions"] = validated_actions
        return attrs

    def _validate_feed_ids(self, feed_ids: list[str]) -> None:
        """Every subscribed feed must exist in the caller's account ; else
        a watch could silently subscribe to a non-existent or cross-account
        feed and still 201. Nested 400 at `feed_ids.N`, mirroring the
        `actions.N` idiom. account_id comes from serializer context (set by
        the view)."""
        if not feed_ids:
            return
        account_id = self.context["account_id"]
        present = FeedService(account_id=account_id).existing_ids(feed_ids)
        errors = {str(i): ["no such feed in this account"] for i, fid in enumerate(feed_ids) if fid not in present}
        if errors:
            raise serializers.ValidationError({"feed_ids": errors})


# ── Output ─────────────────────────────────────────────────────────────


def _action_summary(action: WatchAction) -> WatchActionConfigSummary:
    """Display projection from the typed config; per-row fail-safe so one
    corrupt blob never 500s a list. The set is what `load_config` +
    `summary` can raise on a bad at-rest row: KeyError (kind no longer
    registered), PydanticValidationError (stored shape drift),
    NotImplementedError (a kind missing its summary contract), ValueError
    (a degenerate stored value, e.g. an out-of-range URL port)."""
    try:
        return load_config(action).summary()
    except (KeyError, PydanticValidationError, NotImplementedError, ValueError):
        ref = "preview" if _is_unsaved(action) else action.id  # unsaved preview row has no real id to log
        logger.exception("action %s config failed summary (kind=%s)", ref, action.kind)
        return _EMPTY_SUMMARY


def _action_redacted(action: WatchAction) -> dict[str, Any]:
    """`action.config` through the typed config's redacted_dump; fail-safe
    to a sentinel rather than 500 a `many` list. Same set as
    `_action_summary`."""
    try:
        return load_config(action).redacted_dump()
    except (KeyError, PydanticValidationError, NotImplementedError, ValueError):
        ref = "preview" if _is_unsaved(action) else action.id  # unsaved preview row has no real id to log
        logger.exception("action %s config failed redaction (kind=%s)", ref, action.kind)
        return {"error": "config_unreadable"}


def _is_unsaved(action: WatchAction) -> bool:
    """Django's flag for an in-memory row never written to the DB - used to give a
    dry-run preview row an empty/None id. (`pk is None` won't do: BaseModel assigns
    a ULID default at construction, so an unsaved row still has a pk.)"""
    return action._state.adding


def watch_action_wire(action: WatchAction) -> WatchActionWire:
    """One action's wire shape (opaque redacted config + display summary). `id` is
    empty for an UNSAVED row (a dry-run preview built in memory), real for a
    persisted one - so previews and reads share this one serializer."""
    return WatchActionWire(
        id="" if _is_unsaved(action) else str(action.id),
        kind=str(action.kind),
        rank=action.rank,
        config=_action_redacted(action),
        summary=_action_summary(action),
        created_at=action.created_at,
    )


def watch_action_input_wire(action: WatchActionInput, rank: int) -> WatchActionWire:
    """Wire shape for a not-yet-persisted action (a whole-watch dry-run preview).
    Re-validates the config (shape + policy) so it redacts the SAME normalized
    blob the single-action dry-run does - the two preview surfaces stay
    consistent. Routes the unsaved row through `watch_action_wire`, so redaction +
    summary + the empty id all come from ONE place; no duplicated preview
    serialization in the view.

    Caveat: validate-only, with no by-id merge - so a secret left masked (***) on
    an EDIT previews as *** while the real apply restores the prior value via
    merge_config. Display is identical (both redact to ***) and the persisted
    result is correct; only the preview's literal value is the placeholder. The
    single-action edit path (set_config) DOES merge, so it's exact there."""
    config = validate_config(action.kind, action.config).model_dump(mode="json")
    return watch_action_wire(WatchAction(kind=action.kind, config=config, rank=rank))


def watch_action_mutation(action: WatchAction, *, dry_run: bool) -> WatchActionMutationResponse:
    """One action's add/edit response (real or `?dry_run=true`). `id` reflects
    PERSISTENCE, not the dry_run flag (mirrors `watch_mutation`, which keeps the
    watch id on an update dry-run): a dry-run ADD builds an unsaved row, so there
    is no id yet (None); a dry-run EDIT targets the existing row, whose id is
    unchanged, so it's shown. Spreads `watch_action_wire` (so a new WatchActionWire
    field can't drift out of this response), overriding only id + dry_run."""
    return WatchActionMutationResponse(
        **watch_action_wire(action).model_dump(exclude={"id"}),
        id=None if _is_unsaved(action) else str(action.id),
        dry_run=dry_run,
    )


def run_feed_item_wire(item: FeedItem) -> RunFeedItem:
    """Narrow a FeedItem to the audit log's display fields, for the runs
    response's `feed_items` map (keyed by item id). `feed_id` keys into that
    response's `feeds` map. The view only builds this for items that still
    exist, so a pruned item is simply absent from the map (the run row carries
    `feed_item_id` and renders by it)."""
    data = item.data or {}
    return RunFeedItem(
        title=str(data.get("title", "")),
        url=str(data.get("url", "")),
        external_url=str(data.get("external_url", "")),
        source_label=str(item.source_label),
        feed_id=str(item.feed_id),
        occurred_at=item.occurred_at,  # a FeedItem column (the occurred_* filter axis), not from data
    )


def run_feed_wire(feed: Feed) -> RunFeed:
    """Narrow a Feed for the runs response's `feeds` map (keyed by feed id).
    Few feeds back the many runs on a page, so this is returned once per feed
    instead of repeated on every item."""
    return RunFeed(id=str(feed.id), name=str(feed.name))


def watch_action_run_wire(run: WatchActionRun) -> WatchActionRunWire:
    """One run's wire shape (the audit-log row): pure ids + run state. The
    judged item is in the response's `feed_items` map (key `feed_item_id`), its
    feed in `feeds`. `state` coerces to the WatchActionRunState enum; `result`
    is the opaque kind-specific blob."""
    return WatchActionRunWire(
        id=str(run.id),
        watch_id=str(run.watch_id),
        action_id=str(run.action_id),
        feed_item_id=str(run.feed_item_id),
        state=WatchActionRunState(run.state),
        result=run.result or {},
        error=run.error,
        scheduled_at=run.scheduled_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def watch_action_run_view(
    run: WatchActionRun,
    *,
    feed_item: FeedItem | None = None,
    feed: Feed | None = None,
    action: WatchAction | None = None,
) -> WatchActionRunView:
    """One run's DETAIL shape (`GET /v1/action-activity/<id>`): the run wire plus
    the joined item / feed / action it was judged against. Each is null when
    absent (a pruned item/feed, a removed action), so the row still renders by
    `run.feed_item_id`."""
    return WatchActionRunView(
        run=watch_action_run_wire(run),
        feed_item=run_feed_item_wire(feed_item) if feed_item is not None else None,
        feed=run_feed_wire(feed) if feed is not None else None,
        action=watch_action_wire(action) if action is not None else None,
    )


def _delivery_fields(delivery: WatchActionDelivery) -> dict[str, Any]:
    """The shared list-row fields of a delivery (everything but the payload).
    The string columns (delivery / method / state) coerce to their enums on
    the wire models."""
    return {
        "id": str(delivery.id),
        "watch_id": str(delivery.watch_id),
        "action_id": str(delivery.action_id),
        "delivery": delivery.delivery,
        "method": delivery.method,
        "state": delivery.state,
        "http_status": delivery.http_status,
        "target_host": delivery.target_host,
        "item_count": delivery.item_count,
        "attempt": delivery.attempt,
        "error": delivery.error,
        "started_at": delivery.started_at,
        "completed_at": delivery.completed_at,
        "created_at": delivery.created_at,
    }


def watch_action_delivery_wire(delivery: WatchActionDelivery) -> WatchActionDeliveryWire:
    """One delivery's LIST-row shape (no request_payload ; see the detail view)."""
    return WatchActionDeliveryWire(**_delivery_fields(delivery))


def watch_action_delivery_view(delivery: WatchActionDelivery) -> WatchActionDeliveryView:
    """One delivery's DETAIL shape: the list row plus the stored request_payload."""
    return WatchActionDeliveryView(**_delivery_fields(delivery), request_payload=delivery.request_payload or {})


def watch_wire(watch: Watch, *, feed_ids: list[str]) -> WatchWire:
    """The kind-independent envelope. Tolerates an unsaved instance
    (dry-run): id empty, created_at None."""
    return WatchWire(
        id=str(watch.id),
        name=watch.name,
        is_active=watch.is_active,
        feed_ids=feed_ids,
        user_id=str(watch.user_id),
        created_at=watch.created_at,
    )


def watch_view(watch: Watch, *, feed_ids: list[str], actions: list[WatchAction]) -> WatchView:
    """GET-detail response: envelope + the initial path's ordered chain."""
    return WatchView(
        **watch_wire(watch, feed_ids=feed_ids).model_dump(),
        actions=[watch_action_wire(a) for a in actions],
    )


def watch_mutation(
    watch: Watch, *, feed_ids: list[str], actions: list[WatchAction], dry_run: bool
) -> WatchMutationResponse:
    """Create / edit response: envelope + chain + dry_run, so the CLI's
    confirm-preview shows the resulting watch."""
    return WatchMutationResponse(
        **watch_wire(watch, feed_ids=feed_ids).model_dump(),
        actions=[watch_action_wire(a) for a in actions],
        dry_run=dry_run,
    )
