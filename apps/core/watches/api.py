"""Watch-scope DRF mixins.

Mirrors `feeds.api`. For endpoints under `/v1/watches/<watch_id>/...`
ids land on the request (`request.account_id` from the parent mixin,
`request.watch_id` / `request.action_id` from the URL); the resolved
services + rows are cached_properties on the view so they're loaded once
per request, lazily.
"""

from __future__ import annotations

from functools import cached_property

from rest_framework import status
from rest_framework.exceptions import APIException

from accounts.api import AccountScopedAPIView, AccountScopedRequest

from .models import Watch, WatchAction
from .services import (
    WatchActionBackfillService,
    WatchActionDeliveryService,
    WatchActionRunService,
    WatchActionService,
    WatchService,
)


class WatchScopedRequest(AccountScopedRequest):
    """Typing view of the request once `watch_id` is stashed by
    `WatchScopedAPIView.initial()`."""

    watch_id: str


class ActionScopedRequest(AccountScopedRequest):
    """Typing view once `action_id` is stashed by
    `ActionScopedAPIView.initial()`. Account-scoped, no watch in the URL:
    the action ULID is the leaf id (globally unique), so the action is
    addressed directly rather than nested under its watch."""

    action_id: str


class WatchNotFound(APIException):
    """404 for a watch absent from the caller's account. Same body whether
    it never existed or belongs to another account ; not distinguishing IS
    the account-scoping guarantee."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, watch_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no watch {watch_id}"},
            code=self.default_code,
        )


class WatchActionNotFound(APIException):
    """404 for an action absent from the (account, watch) it's scoped to."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, action_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no action {action_id}"},
            code=self.default_code,
        )


class WatchActionDeliveryNotFound(APIException):
    """404 for a delivery absent from the caller's account."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, delivery_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no delivery {delivery_id}"},
            code=self.default_code,
        )


class WatchActionRunNotFound(APIException):
    """404 for a run (activity entry) absent from the caller's account."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, run_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no activity {run_id}"},
            code=self.default_code,
        )


class WatchActionBackfillNotFound(APIException):
    """404 for a backfill job absent from the caller's account."""

    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"

    def __init__(self, backfill_id: str) -> None:
        super().__init__(
            detail={"error": "not_found", "detail": f"no backfill {backfill_id}"},
            code=self.default_code,
        )


class WatchSvcMixin:
    """Per-request `watch_svc` cached_property ; usable on any view that
    knows the account but doesn't have a watch-id in its URL."""

    request: AccountScopedRequest

    @cached_property
    def watch_svc(self) -> WatchService:
        return WatchService(account_id=self.request.account_id)

    @cached_property
    def action_svc(self) -> WatchActionService:
        return WatchActionService(account_id=self.request.account_id)

    @cached_property
    def run_svc(self) -> WatchActionRunService:
        return WatchActionRunService(account_id=self.request.account_id)

    @cached_property
    def delivery_svc(self) -> WatchActionDeliveryService:
        return WatchActionDeliveryService(account_id=self.request.account_id)

    @cached_property
    def backfill_svc(self) -> WatchActionBackfillService:
        return WatchActionBackfillService(account_id=self.request.account_id)


class WatchScopedAPIView(WatchSvcMixin, AccountScopedAPIView):
    """APIView for endpoints scoped to one watch within the caller's
    account. After `initial()`: `request.account_id` + `request.watch_id`.
    `self.watch` is the resolved row (raises WatchNotFound on first
    access if absent), cached so repeated access is one DB hit."""

    request: WatchScopedRequest

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.watch_id = kwargs["watch_id"]

    @cached_property
    def watch(self) -> Watch:
        try:
            return self.watch_svc.get(self.request.watch_id)
        except Watch.DoesNotExist as exc:
            raise WatchNotFound(self.request.watch_id) from exc


class ActionScopedAPIView(WatchSvcMixin, AccountScopedAPIView):
    """APIView for endpoints addressing one action by its globally-unique id:
    `/v1/actions/<action_id>[/...]`. The action ULID is the leaf, so no watch
    id in the route ; account scoping is the isolation guarantee (the action
    belongs to a path -> watch in the caller's account, and `action_svc.get`
    is account-bounded — another account's id 404s identically to a missing
    one). `self.action` is the resolved row, cached for one DB hit."""

    request: ActionScopedRequest

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        request.action_id = kwargs["action_id"]

    @cached_property
    def action(self) -> WatchAction:
        try:
            return self.action_svc.get(self.request.action_id)
        except WatchAction.DoesNotExist as exc:
            raise WatchActionNotFound(self.request.action_id) from exc
