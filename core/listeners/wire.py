"""Canonical wire shapes — the SINGLE SOURCE the CLI codegens from.

Two layers, deliberately separate (see project memory
`project_schema_authority_northstar`):

- `ListenerWire` is the transport/round-trip record. `data` is opaque
  (`dict[str, Any]`) on purpose: list/get/edit must round-trip a
  listener whose `kind` the client may not know (old client vs newer
  server). Codegen must NOT type `data`'s interior here.

- The per-kind config schemas are published SEPARATELY (built from
  `listeners.registry`), and the CLI codegens typed per-kind models +
  a `CONFIG_BY_KIND` map under a common `ListenerConfig` base - for
  *constructing* a config with types and discovering kinds/fields
  without the `core` repo. Additive: never gate round-trip on it.

`dump_wire_schema` emits both as one JSON-Schema bundle; the CLI's
generated module is produced from it (staleness-guarded), so the
stable shapes are declared once, here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# Wire models declare NO field defaults on purpose: the published
# contract is "exactly what the server always sends", so the generated
# CLI models come out required (not `T | None = <default>`, which would
# both fail the type-checker and force `or []` guards everywhere).
# Genuinely-nullable fields are `T | None` (still required-present,
# value may be null); server-side fallbacks/defaults live in the
# builders, not the schema.


class WireSummary(BaseModel):
    """Display projection the server always fully populates (empty
    strings/lists, never absent). Distinct from `configs.
    ListenerConfigSummary` (which keeps defaults for the server-side
    fallback path) so the published schema stays default-free."""

    streams: list[str]
    notifiers: list[str]
    engine: str


class ListenerWire(BaseModel):
    """One listener as it goes over the wire (list / create / dry-run /
    get / edit all serialize through this). Tolerates an unsaved
    instance: `created_at` is null pre-save; `id` is null on the create
    dry-run preview (no persisted row yet)."""

    id: str | None
    name: str
    instructions: str
    kind: str
    delivery_mode: str
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None
    next_poll_at: datetime | None
    last_digest_at: datetime | None
    next_digest_at: datetime | None
    # creator, for audit/display. Account-scoped reads mean this is NOT
    # an ownership filter (see ListenerService).
    user_id: str
    # Opaque on purpose - see module docstring. Schema describes this as
    # a free-form object; the CLI never types its interior here.
    data: dict[str, Any]
    created_at: datetime | None


# ── Response envelopes (also single-sourced; CLI codegens these too) ──
# Each model's published JSON Schema is flat (fields expanded), so the
# generated CLI models carry no inheritance coupling.


class ListenerDetailWire(ListenerWire):
    """GET /v1/listeners/<id>: the record + its display summary."""

    summary: WireSummary


class ListenerMutationWire(ListenerWire):
    """create / dry-run / edit response: record + summary + the explicit
    `dry_run` marker."""

    summary: WireSummary
    dry_run: bool


class ListenerListWire(BaseModel):
    """GET /v1/listeners: the list envelope."""

    items: list[ListenerWire]
