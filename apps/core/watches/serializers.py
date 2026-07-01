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
from feeds.services import FeedService
from openmagpie_schema.watch import (
    WatchActionInput,
    WatchActionMutationResponse,
    WatchActionWire,
    WatchMutationResponse,
    WatchView,
    WatchWire,
    build_watch_action_input,
    build_watch_action_wire,
)
from openmagpie_schema.watch_actions import WatchActionConfigSummary
from watches.models import Watch, WatchAction
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
            validated_actions.append(
                build_watch_action_input(id=action_id, kind=kind, config=typed.model_dump(mode="json"))
            )
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


class WatchSetActiveSerializer(serializers.Serializer):
    """PATCH /v1/watches/<id> body: the pause/resume toggle. Just the one bit, so the
    feed set + action chain are untouched (a full PUT would replace the chain)."""

    is_active = serializers.BooleanField()


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


def _action_redacted(action: WatchAction) -> dict[str, Any] | None:
    """`action.config` through the typed config's redacted_dump; fail-safe to None
    (a corrupt at-rest config the wire renders as `config: null`) rather than 500
    a `many` list. None (not a sentinel dict) because the wire config is now a
    typed union: a `{"error": ...}` dict can't validate as 3 of the 4 kinds
    (required fields), so it would re-raise; None is the member's degrade. Same
    catch set as `_action_summary`."""
    try:
        return load_config(action).redacted_dump()
    except (KeyError, PydanticValidationError, NotImplementedError, ValueError):
        ref = "preview" if _is_unsaved(action) else action.id  # unsaved preview row has no real id to log
        logger.exception("action %s config failed redaction (kind=%s)", ref, action.kind)
        return None


def _is_unsaved(action: WatchAction) -> bool:
    """Django's flag for an in-memory row never written to the DB - used to give a
    dry-run preview row an empty/None id. (`pk is None` won't do: BaseModel assigns
    a ULID default at construction, so an unsaved row still has a pk.)"""
    return action._state.adding


def watch_action_wire(action: WatchAction) -> WatchActionWire | None:
    """One action's wire shape (redacted typed config + display summary). `id` is
    empty for an UNSAVED row (a dry-run preview built in memory), real for a
    persisted one - so previews and reads share this one serializer.

    Two at-rest degrades keep a `many` read from 500-ing on one bad row:
    - a config that no longer types renders with `config=None` (`_action_redacted`
      returns None; the build re-degrades to None if a loadable config still fails
      to validate into its member, belt-and-suspenders; the summary already fell
      back to empty independently).
    - a `kind` that isn't a known action kind can't select ANY union member, so
      the row is SKIPPED (returns None) and the caller drops it. The KNOWN_KINDS
      invariant test rules this out for live data; this is the corrupt-kind-column
      backstop, mirroring the run wire's kind guard."""
    kind = str(action.kind)
    ref = "preview" if _is_unsaved(action) else action.id
    if kind not in KNOWN_KINDS:
        logger.error("action %s has an unrenderable kind=%s; skipping the row", ref, kind)
        return None
    common = {
        "id": "" if _is_unsaved(action) else str(action.id),
        "kind": kind,
        "rank": action.rank,
        "summary": _action_summary(action),
        "created_at": action.created_at,
    }
    try:
        return build_watch_action_wire(config=_action_redacted(action), **common)
    except (PydanticValidationError, ValueError):
        logger.exception("action %s config failed to type (kind=%s); rendering config=null", ref, kind)
        return build_watch_action_wire(config=None, **common)


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
    # `action.config` is the typed union member's config model; the registry
    # re-runs shape + policy from its dict form (the persisted blob is kind-less).
    config = validate_config(action.kind, action.config.model_dump(mode="json")).model_dump(mode="json")
    # validate_config above rejects an unknown kind, so watch_action_wire always
    # builds here (never the corrupt-kind None); assert narrows for the type.
    wire = watch_action_wire(WatchAction(kind=action.kind, config=config, rank=rank))
    assert wire is not None
    return wire


def watch_action_mutation(action: WatchAction, *, dry_run: bool) -> WatchActionMutationResponse:
    """One action's add/edit response (real or `?dry_run=true`). NESTS the typed
    action node under `action` (the response is no longer a flat WatchActionWire,
    since the union alias can't be subclassed to null the id). The nested node's
    `id` reflects PERSISTENCE, not the dry_run flag (mirrors `watch_mutation`,
    which keeps the watch id on an update dry-run): a dry-run ADD builds an
    unsaved row, so `action.id` is "" ; a dry-run EDIT targets the existing row,
    whose id is unchanged, so it's shown."""
    # The mutated action's kind was validated on write, so the wire always builds.
    wire = watch_action_wire(action)
    assert wire is not None
    return WatchActionMutationResponse(action=wire, dry_run=dry_run)


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
        actions=[wire for a in actions if (wire := watch_action_wire(a)) is not None],
    )


def watch_mutation(
    watch: Watch, *, feed_ids: list[str], actions: list[WatchAction], dry_run: bool
) -> WatchMutationResponse:
    """Create / edit response: envelope + chain + dry_run, so the CLI's
    confirm-preview shows the resulting watch."""
    return WatchMutationResponse(
        **watch_wire(watch, feed_ids=feed_ids).model_dump(),
        actions=[wire for a in actions if (wire := watch_action_wire(a)) is not None],
        dry_run=dry_run,
    )
