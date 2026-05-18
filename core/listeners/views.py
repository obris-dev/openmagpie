"""HTTP entry points for /v1/listeners.

`ListenerListCreateView` handles POST (create) and GET (list). Both
gated on `IsAuthenticated`; account scoping happens via
`ListenerService(account_id=...)` keyed off the request user's primary
account.

Listener-kind-specific validation lives in the Pydantic registry
(`listeners.registry`); the serializer just delegates to it.
"""

from __future__ import annotations

import logging

from accounts.services import AccountService
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Listener
from .serializers import (
    ListenerCreateSerializer,
    wire_detail,
    wire_list,
    wire_mutation,
)
from .services.listeners import ListenerService

logger = logging.getLogger("listeners")

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    """Parse a query-string flag. Absent or anything outside the truthy
    set reads as False, so `?dry_run=0` / no param creates for real."""
    return value is not None and value.strip().lower() in _TRUTHY


def _no_primary_account_response(user_id: str) -> Response:
    """Shared 500 for the 'user has no primary account' invariant.

    Signup invariants should have created one; its absence is account
    corruption, not a normal empty state. POST and GET both return THIS
    (identical body + status) so the two endpoints can't drift - GET
    masking it as `{"items": []}` would turn a corruption into a silent
    'you have no listeners' data-loss report.
    """
    logger.error("user %s has no primary account", user_id)
    return Response(
        {
            "error": "no_primary_account",
            "detail": "current user has no primary account",
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


class ListenerListCreateView(APIView):
    """POST  /v1/listeners,  create a new listener for the caller's account.
    GET   /v1/listeners,  list listeners in the caller's account.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ListenerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            return _no_primary_account_response(str(request.user.id))

        svc = ListenerService(account_id=account_id)

        if _is_truthy(request.query_params.get("dry_run")):
            # Validate-only: build the would-be record in memory and
            # return it WITHOUT persisting. `build()` runs the IDENTICAL
            # serializer + service validation as create, so the preview
            # is faithful for *validation*. It does not guarantee save
            # success (persistence can still fail); the preview is a
            # validation preview, not a create-success promise.
            preview = svc.build(
                user_id=str(request.user.id),
                name=d["name"],
                instructions=d["instructions"],
                kind=d["kind"],
                delivery_mode=d["delivery_mode"],
                poll_interval_seconds=d["poll_interval_seconds"],
                data=d["data"],
            )
            # drop_id: no persisted row yet -> id serializes as null so
            # a client never reads a meaningless placeholder.
            return Response(
                wire_mutation(preview, dry_run=True, drop_id=True),
                status=status.HTTP_200_OK,
            )

        listener = svc.create(
            user_id=str(request.user.id),
            name=d["name"],
            instructions=d["instructions"],
            kind=d["kind"],
            delivery_mode=d["delivery_mode"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
        )
        # Symmetric `dry_run: False` so a client can branch on the body
        # alone (the 201 vs 200 status already distinguishes them).
        return Response(
            wire_mutation(listener, dry_run=False, drop_id=False),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            return _no_primary_account_response(str(request.user.id))
        listeners = ListenerService(account_id=account_id).list()
        return Response(wire_list(listeners))


def _not_found_response(listener_id: str) -> Response:
    """404 for a listener absent from the caller's account. Same body
    whether it never existed or belongs to another account - not
    distinguishing IS the account-scoping guarantee."""
    return Response(
        {"error": "not_found", "detail": f"no listener {listener_id}"},
        status=status.HTTP_404_NOT_FOUND,
    )


class ListenerDetailView(APIView):
    """GET / PUT / DELETE /v1/listeners/<id>, all account-scoped.

    PUT is full-replace edit and mirrors create's contract: same
    envelope validation, same `?dry_run=true` preview, same body shape
    (+ `summary`, `dry_run`). `kind` is immutable. Watermarks and `***`
    secrets carry forward (ListenerService.build_update); id / created_at
    / user_id / poll-state columns never change.
    """

    permission_classes = [permissions.IsAuthenticated]

    def _resolve(self, request, listener_id: str):
        """`(svc, listener)` or an error `Response`. Centralizes the
        account-scope + existence checks all three verbs share."""
        account_id = AccountService.Global.primary_account_id_for(
            user_id=str(request.user.id)
        )
        if account_id is None:
            return _no_primary_account_response(str(request.user.id))
        svc = ListenerService(account_id=account_id)
        try:
            return svc, svc.get(listener_id)
        except Listener.DoesNotExist:
            return _not_found_response(listener_id)

    def get(self, request, listener_id: str):
        resolved = self._resolve(request, listener_id)
        if isinstance(resolved, Response):
            return resolved
        _, listener = resolved
        return Response(wire_detail(listener), status=status.HTTP_200_OK)

    def put(self, request, listener_id: str):
        resolved = self._resolve(request, listener_id)
        if isinstance(resolved, Response):
            return resolved
        svc, listener = resolved

        serializer = ListenerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        if d["kind"] != listener.kind:
            # Immutable: a kind change swaps the config schema, making
            # watermark/secret preservation ill-defined. delete+recreate
            # is the path for switching kind.
            return Response(
                {
                    "error": "kind_immutable",
                    "detail": (
                        f"listener kind is {listener.kind!r} and cannot be "
                        f"changed (requested {d['kind']!r})"
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )

        edit_kwargs = dict(
            name=d["name"],
            instructions=d["instructions"],
            delivery_mode=d["delivery_mode"],
            poll_interval_seconds=d["poll_interval_seconds"],
            data=d["data"],
        )

        if _is_truthy(request.query_params.get("dry_run")):
            # Same validate-only contract as create's dry-run, but id /
            # created_at are real (existing row), so unlike create we do
            # NOT strip id - the preview shows the actual listener.
            preview = svc.build_update(listener, **edit_kwargs)
            return Response(
                wire_mutation(preview, dry_run=True, drop_id=False),
                status=status.HTTP_200_OK,
            )

        updated = svc.update(listener, **edit_kwargs)
        return Response(
            wire_mutation(updated, dry_run=False, drop_id=False),
            status=status.HTTP_200_OK,
        )

    def delete(self, request, listener_id: str):
        resolved = self._resolve(request, listener_id)
        if isinstance(resolved, Response):
            return resolved
        svc, listener = resolved
        svc.delete(listener)
        return Response(status=status.HTTP_204_NO_CONTENT)
