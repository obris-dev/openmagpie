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


class ListenerWire(BaseModel):
    """One listener as it goes over the wire (list / create / dry-run /
    get / edit all serialize through this). Tolerates an unsaved
    instance: `created_at` is None pre-save; `id` is the empty-string
    ULID placeholder (the create dry-run view strips it)."""

    id: str
    name: str
    instructions: str
    kind: str
    delivery_mode: str
    is_active: bool
    poll_interval_seconds: int
    last_polled_at: datetime | None = None
    next_poll_at: datetime | None = None
    last_digest_at: datetime | None = None
    next_digest_at: datetime | None = None
    # creator, for audit/display. Account-scoped reads mean this is NOT
    # an ownership filter (see ListenerService).
    user_id: str
    # Opaque on purpose - see module docstring. Schema describes this as
    # a free-form object; the CLI never types its interior here.
    data: dict[str, Any] = {}
    created_at: datetime | None = None
