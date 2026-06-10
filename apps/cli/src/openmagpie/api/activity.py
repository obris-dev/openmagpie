"""Activity (run audit) API resource client.

The flat observability noun over one action's `WatchActionRun`s: the list
(`/v1/actions/<id>/activity`, whose first page also carries the summary
rollup) and one run's detail (`/v1/action-activity/<id>`). Shapes live once in
`openmagpie_schema.watch`; the server is the authority.
"""

from __future__ import annotations

from openmagpie_schema.watch import WatchActionRunListResponse, WatchActionRunView

from .. import routes
from ..http import MagpieClient
from ._params import list_params


class ActivityApi:
    def __init__(self, http: MagpieClient) -> None:
        self._http = http

    def list(
        self,
        action_id: str,
        *,
        state: str | None = None,
        after: str | None = None,
        limit: int | None = None,
        window: str | None = None,
    ) -> WatchActionRunListResponse:
        # `window` is the summary preset (server resolves it to bounds); the
        # first page carries the summary rollup, paged calls don't.
        params = list_params(state=state, after=after, limit=limit, window=window)
        raw = self._http.get(routes.actions.runs(action_id), params=params)
        return WatchActionRunListResponse.model_validate(raw)

    def get(self, activity_id: str) -> WatchActionRunView:
        raw = self._http.get(routes.action_activity.detail(activity_id))
        return WatchActionRunView.model_validate(raw)
