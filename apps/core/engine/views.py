"""HTTP entry points for /v1/engines.

`EngineListView` reports each registered engine's reachability so a
client can pre-flight the LLM before creating a semantic-filter action
and getting silent 500-per-judge-cycle once polling starts.

Auth-gated but NOT account-scoped — engine identity is system-level,
not per-tenant. The endpoint never returns secrets (URLs aren't
considered secret here; they're either localhost in dev or whatever
the operator set in ENGINE_BASE_URL).
"""

from __future__ import annotations

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from openmagpie_schema.engine import EngineListResponse

from . import registry


class EngineListView(APIView):
    """GET /v1/engines — registered engines + reachability snapshot.

    Calls `engine.status()` once per registered kind. `status()` never
    raises, so an unreachable upstream renders as
    `available=False, unreachable_reason=...` — the endpoint always
    returns 200 (this is a "what is the state of the world" probe, not
    an action that can fail)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        items = [registry.get(kind).status() for kind in registry.kinds()]
        return Response(EngineListResponse(items=items).model_dump(mode="json"))
