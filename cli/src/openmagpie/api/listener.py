"""Listeners API resource client.

Response shapes are GENERATED from the server's published wire schema
(`_generated_wire.py`, via `make dev-cli-types`) - declared once
server-side, never hand-copied here. Only the *request* envelope
(`ListenerEnvelope`) and the opaque `ConfigBlob` are hand-written: the
envelope's server twin is a DRF serializer (not Pydantic), and the
config interior is deliberately opaque (server is its sole validator).
"""

from __future__ import annotations

import builtins
from typing import Any

from pydantic import BaseModel

from .. import routes
from ..http import MagpieClient
from ._generated_wire import (
    ListenerDetailWire,
    ListenerListWire,
    ListenerMutationWire,
    ListenerWire,
    WireSummary,
)

# Back-compat names for command code; the shapes are the generated,
# single-sourced wire models.
ListenerDetail = ListenerDetailWire
ListenerMutationResponse = ListenerMutationWire

__all__ = [
    "ConfigBlob",
    "ListenerApi",
    "ListenerDetail",
    "ListenerDetailWire",
    "ListenerEnvelope",
    "ListenerMutationResponse",
    "ListenerMutationWire",
    "ListenerWire",
    "WireSummary",
]

type ConfigBlob = dict[str, Any]
"""A listener's kind-specific `data` config.

Opaque to the CLI on purpose: the server (Pydantic registry, keyed by
the envelope's `kind`) is the sole validator. Typing the *interior*
here would mirror that schema and drift the moment the server adds a
kind. The CLI only carries it - YAML round-trip for edit, server emits
a typed `summary` for display - never reads a field.
"""


class ListenerEnvelope(BaseModel):
    """The kind-INDEPENDENT request envelope. Hand-written on purpose:
    its server twin is `ListenerCreateSerializer` (DRF, not Pydantic),
    so it isn't part of the generated wire schema. Stable and CLI-owned;
    only `data`'s interior is opaque (`ConfigBlob`). `kind` is the
    discriminator the server lanes on. Extra keys ignored; `data` passed
    through untouched for the server to validate."""

    name: str
    instructions: str
    kind: str
    delivery_mode: str
    poll_interval_seconds: int
    data: ConfigBlob = {}

    model_config = {"extra": "ignore"}


class ListenerApi:
    """Resource client for `/v1/listeners`. Every response is parsed
    through a generated wire model (single source; no drift)."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def create(self, body: dict[str, Any], *, dry_run: bool = False) -> ListenerMutationWire:
        """POST a listener. `dry_run=True` adds `?dry_run=true` (server
        validates + returns the would-be record WITHOUT persisting).
        Validation errors -> `ApiError` (status=400) with per-field
        detail in `e.body`."""
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.post(routes.listeners.collection, json_body=body, params=params)
        return ListenerMutationWire.model_validate(raw)

    def list(self) -> builtins.list[ListenerWire]:
        # builtins.list: the method is named `list`, which shadows the
        # builtin inside its own (deferred) annotation scope.
        """List listeners in the caller's account, newest-first."""
        raw = self._http.get(routes.listeners.collection)
        return ListenerListWire.model_validate(raw).items

    def get(self, listener_id: str) -> ListenerDetailWire:
        """GET one listener (account-scoped). 404 -> ApiError(status=404)."""
        raw = self._http.get(routes.listeners.detail(listener_id))
        return ListenerDetailWire.model_validate(raw)

    def update(self, listener_id: str, body: dict[str, Any], *, dry_run: bool = False) -> ListenerMutationWire:
        """PUT a full-replace edit. Same contract as `create`. The server
        keeps `kind` immutable and preserves watermarks + `***` secrets
        the operator left masked."""
        params = {"dry_run": "true"} if dry_run else None
        raw = self._http.put(routes.listeners.detail(listener_id), json_body=body, params=params)
        return ListenerMutationWire.model_validate(raw)

    def delete(self, listener_id: str) -> None:
        """DELETE one listener. 204 on success; 404 -> ApiError."""
        self._http.delete(routes.listeners.detail(listener_id))
