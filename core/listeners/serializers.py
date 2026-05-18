"""Listeners API wire shapes.

Input is a DRF serializer (`ListenerCreateSerializer`) because that's
the HTTP request-validation boundary: field rules + the flat-path 400
errors, delegating the `data` blob to the Pydantic registry.

Output is NOT a DRF serializer. `listener_wire()` is a plain builder:
the only non-trivial part of the response (the redacted config) is
already owned by the typed Pydantic config, so a DRF output serializer
was just a hand-maintained field-mirror - and its field literally named
`data` shadowed `BaseSerializer.data`, needing a cast to work around.
One builder, one source of truth, used by list/create/dry-run/get/edit.
"""

from __future__ import annotations

import logging
from typing import Any

from listeners.models import Listener
from listeners.registry import get_config_class
from listeners.wire import ListenerWire
from pydantic import ValidationError as PydanticValidationError
from rest_framework import serializers

from .models.listener import MIN_POLL_INTERVAL_SECONDS

logger = logging.getLogger("listeners")


# ── Input ──────────────────────────────────────────────────────────────


class ListenerCreateSerializer(serializers.Serializer):
    """Envelope for POST /v1/listeners.

    The kind-specific config blob arrives as `data`; we validate it via
    the Pydantic registry so each Listener kind owns its own schema.
    """

    name = serializers.CharField(max_length=255, trim_whitespace=True)
    # min_length floors out "" / "." / "x": instructions are fed verbatim
    # to the engine as the relevance criteria, a sub-meaningful value
    # silently burns LLM tokens producing garbage verdicts every poll.
    # The floor is deliberately low, it catches junk, not short prose.
    instructions = serializers.CharField(min_length=8, trim_whitespace=True)
    kind = serializers.CharField(max_length=32)
    delivery_mode = serializers.ChoiceField(
        choices=[m.value for m in Listener.DeliveryMode],
        default=Listener.DeliveryMode.INSTANT.value,
    )
    poll_interval_seconds = serializers.IntegerField(
        min_value=MIN_POLL_INTERVAL_SECONDS,
        default=300,
    )
    data = serializers.DictField(child=serializers.JSONField())

    def validate_kind(self, value: str) -> str:
        try:
            get_config_class(value)
        except KeyError:
            raise serializers.ValidationError(f"unknown listener kind {value!r}")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        # `data` is validated against the kind-specific Pydantic class.
        # We do it in `validate()` (not `validate_data()`) because we
        # need the already-validated `kind` field to pick the schema.
        config_class = get_config_class(attrs["kind"])
        try:
            validated = config_class.model_validate(attrs["data"])
        except PydanticValidationError as exc:
            raise serializers.ValidationError(
                {"data": _pydantic_errors_to_drf(exc)}
            ) from exc
        # Replace the raw dict with the normalized Pydantic dump so the
        # service layer stores a canonical shape regardless of input
        # ordering or omitted defaults.
        attrs["data"] = validated.model_dump(mode="json")
        return attrs


def _loc_to_path(loc: tuple[Any, ...]) -> str:
    """Render a Pydantic `loc` tuple as a flat field path.

    `('streams', 0, 'spec', 'kind')` -> `streams[0].spec.kind`. Integer
    segments are list indices and become `[i]`; named segments are
    dot-joined. This is the exact shape `cli/AGENTS.md` documents and the
    CLI error printer expects, one key per leaf, no nested dicts (so
    sibling errors under the same parent can't collide and array-element
    paths render as `streams[0]...`, not `streams.0...`).
    """
    parts: list[str] = []
    for seg in loc:
        if isinstance(seg, int):
            parts.append(f"[{seg}]")
        else:
            parts.append(str(seg) if not parts else f".{seg}")
    return "".join(parts) or "__root__"


def _pydantic_errors_to_drf(exc: PydanticValidationError) -> dict[str, Any]:
    """Re-shape Pydantic's error list into DRF's `{path: [messages]}` dict.

    Flat, one key per leaf path (see `_loc_to_path`). Multiple messages
    for the same path accumulate in the list.
    """
    out: dict[str, list[str]] = {}
    for err in exc.errors():
        out.setdefault(_loc_to_path(tuple(err["loc"])), []).append(err["msg"])
    return out


# ── Output ─────────────────────────────────────────────────────────────


def _redacted_data(listener: Listener) -> dict[str, Any]:
    """`listener.data` validated through the kind's typed config and
    redacted (the config owns what's secret - see
    configs.NotifierSpecBase.redacted).

    Per-row fail-safe: re-validation can fail for a single row (unknown
    kind, data drift, a settings-dependent validator like the webhook
    URL check). That MUST NOT 500 a `many` list - it would abort the
    whole account's list, hiding healthy listeners. On failure log and
    return a sentinel, NEVER raw `listener.data` (that would leak
    unredacted webhook secrets). The row stays visible via its model
    columns so the operator sees it exists and is broken.
    """
    try:
        config = get_config_class(str(listener.kind)).model_validate(
            listener.data or {}
        )
        return config.redacted_dump()
    except Exception:
        logger.exception(
            "listener %s data failed redaction (kind=%s); returning sentinel",
            listener.id,
            listener.kind,
        )
        return {"error": "config_unreadable"}


def listener_wire(listener: Listener) -> dict[str, Any]:
    """The single source for a Listener's wire shape. list / create /
    dry-run / get / edit all go through here, so the response can't drift
    between endpoints.

    Built and dumped through `wire.ListenerWire`: that Pydantic model is
    the canonical shape the CLI codegens from, so the contract is
    declared once (in `wire.py`) and never hand-copied across the
    boundary. `mode="json"` ISO-encodes datetimes. Tolerates an unsaved
    instance (dry-run): `created_at` is None pre-save; `id` is the
    empty-string ULID placeholder (the create dry-run view strips it).
    """
    return ListenerWire(
        id=listener.id,
        name=listener.name,
        instructions=listener.instructions,
        kind=listener.kind,
        delivery_mode=listener.delivery_mode,
        is_active=listener.is_active,
        poll_interval_seconds=listener.poll_interval_seconds,
        last_polled_at=listener.last_polled_at,
        next_poll_at=listener.next_poll_at,
        last_digest_at=listener.last_digest_at,
        next_digest_at=listener.next_digest_at,
        user_id=listener.user_id,
        data=_redacted_data(listener),
        created_at=listener.created_at,
    ).model_dump(mode="json")
