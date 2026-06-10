"""Delivery (outbound HTTP call audit) API resource client.

The flat observability noun over one action's `WatchActionDelivery`s: the list
(`/v1/actions/<id>/deliveries`, lean rows) and one delivery's detail
(`/v1/action-deliveries/<id>`, including the sent request payload). Shapes live
once in `openmagpie_schema.watch`; the server is the authority.
"""

from __future__ import annotations

from openmagpie_schema.watch import WatchActionDeliveryListResponse, WatchActionDeliveryView

from .. import routes
from ..http import MagpieClient
from ._params import list_params


class DeliveryApi:
    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def list(
        self,
        action_id: str,
        *,
        state: str | None = None,
        after: str | None = None,
        limit: int | None = None,
    ) -> WatchActionDeliveryListResponse:
        params = list_params(state=state, after=after, limit=limit)
        raw = self._http.get(routes.actions.deliveries(action_id), params=params)
        return WatchActionDeliveryListResponse.model_validate(raw)

    def get(self, delivery_id: str) -> WatchActionDeliveryView:
        raw = self._http.get(routes.deliveries.detail(delivery_id))
        return WatchActionDeliveryView.model_validate(raw)
