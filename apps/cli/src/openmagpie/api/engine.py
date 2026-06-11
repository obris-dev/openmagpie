"""Engines API resource client.

Wraps `/v1/engines`. Response models live in the shared
`openmagpie_schema.engine` module and are imported verbatim.

Intended to back a future pre-flight check (surfacing "LLM unreachable"
at config time beats discovering it via 500-per-judge-cycle once polling
starts); no `magpie` command consumes it yet.
"""

from __future__ import annotations

from openmagpie_schema.engine import EngineListResponse, EngineStatus

from .. import routes
from ..http import MagpieClient

__all__ = ["EngineApi", "EngineListResponse", "EngineStatus"]


class EngineApi:
    """Resource client for `/v1/engines`."""

    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def list(self) -> list[EngineStatus]:
        """GET /v1/engines — registered engines + reachability snapshot.

        Returns the unwrapped items list; the envelope is internal and
        callers always want the per-engine entries.
        """
        raw = self._http.get(routes.engines.collection)
        return EngineListResponse.model_validate(raw).items
