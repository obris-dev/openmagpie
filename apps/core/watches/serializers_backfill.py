"""Backfill read-path builder: the `WatchActionBackfill` -> wire shape.

Split from `views_backfill.py` (the endpoints) so the model->wire mapping lives in
a serializer module like every sibling (`serializers.py`, `serializers_audit.py`,
`feeds/serializers.py`), not inline in a view. Populates the shared
`openmagpie_schema.backfill.BackfillJob`, so the server is its authority and the CLI
imports the same class.
"""

from __future__ import annotations

from rest_framework import serializers

from openmagpie_schema.backfill import BackfillJob

from .models import WatchActionBackfill


class BackfillInputSerializer(serializers.Serializer):
    """POST /v1/actions/<id>/backfill body. `replace` is a real `BooleanField` so its
    spelling is coerced parser-agnostically: a JSON bool, a form-encoded "true"/"false"
    (DRF's FormParser makes every value a string), or a stray JSON string all resolve
    correctly, and garbage 400s. NOT `bool(body.get(...))`: `bool("false")` is truthy,
    so the string "false" would wrongly trigger the destructive whole-chain delete;
    NOT `is True` either, which would reject the legit string "true" from a form client.
    The window values stay RAW strings (a duration like `7d` or ISO); the server owns
    their authoritative resolution, so they're plain CharFields here."""

    replace = serializers.BooleanField(required=False, default=False)
    occurred_since = serializers.CharField(required=False, allow_blank=True)
    occurred_until = serializers.CharField(required=False, allow_blank=True)
    completed_since = serializers.CharField(required=False, allow_blank=True)
    completed_until = serializers.CharField(required=False, allow_blank=True)


def backfill_job_wire(job: WatchActionBackfill) -> BackfillJob:
    """Model row -> `BackfillJob`. One place so the detail + list responses render
    identically. pydantic coerces `state`/`kind` (bare CharFields) into their enums."""
    return BackfillJob(
        id=str(job.id),
        state=job.state,
        target_action_id=str(job.target_action_id),
        source_action_id=str(job.source_action_id),
        source_is_head=job.source_is_head,
        kind=job.kind,
        replace=job.replace,
        occurred_since=job.occurred_since,
        occurred_until=job.occurred_until,
        completed_since=job.completed_since,
        completed_until=job.completed_until,
        matched=job.matched,
        present=job.present,
        pruned=job.pruned,
        deleted=job.deleted,
        enqueued=job.enqueued,
        error=str(job.error),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
