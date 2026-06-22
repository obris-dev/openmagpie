"""HTTP entry points for /v1/watches.

`WatchListCreateView` handles POST (create) + GET (list). `WatchDetailView`
handles GET / PUT / DELETE on `/v1/watches/<id>`. `WatchActionsView` covers
the chain-level `/v1/watches/<id>/actions` (list + add). Per-action ops
(`ActionDetailView` edit/remove, `ActionRunsView` runs) live at
`/v1/actions/<action_id>` — addressed by the action's own id, account-scoped.

The `/v1/watches/<id>/...` views inherit `WatchScopedAPIView` and read
`self.watch` directly ; a missing watch raises `WatchNotFound`, DRF
converts to 404. The `/v1/actions/...` views inherit `ActionScopedAPIView`.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.response import Response

from accounts.api import AccountScopedAPIView
from common.api_params import is_truthy, parse_limit
from common.pydantic_errors import pydantic_errors_to_drf
from openmagpie_schema.watch import (
    WatchActionInput,
    WatchListResponse,
)
from telemetry import events as telemetry_events
from telemetry.constants import Surface

from .api import (
    ActionScopedAPIView,
    WatchScopedAPIView,
    WatchSvcMixin,
)
from .policy import PolicyError
from .registry import KNOWN_KINDS
from .serializers import (
    WatchCreateSerializer,
    watch_action_wire,
    watch_mutation,
    watch_view,
    watch_wire,
)
from .services.watches._actions import ConcurrentChainError

logger = logging.getLogger("watches")


def _validate_kind(kind: object) -> Response | None:
    """400 Response if `kind` (the action's top-level discriminator on the
    sub-router request) isn't a known action kind ; None if it's valid.
    The chain-write serializer does the equivalent check per action."""
    if kind not in KNOWN_KINDS:
        return Response(
            {"kind": [f"unknown action kind {kind!r}; known: {sorted(KNOWN_KINDS)}"]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return None


class WatchListCreateView(WatchSvcMixin, AccountScopedAPIView):
    """POST /v1/watches (create), GET /v1/watches (list)."""

    def post(self, request):
        serializer = WatchCreateSerializer(data=request.data, context={"account_id": request.account_id})
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        actions: list[WatchActionInput] = d["actions"]

        if is_truthy(request.query_params.get("dry_run")):
            preview = self.watch_svc.build(name=d["name"], is_active=d["is_active"])
            body = watch_mutation(preview, feed_ids=d["feed_ids"], actions=[], dry_run=True).model_dump(mode="json")
            # Preview the chain from the validated inputs (no rows persisted).
            body["actions"] = [_preview_action_wire(a, rank) for rank, a in enumerate(actions)]
            body.pop("id", None)
            return Response(body, status=status.HTTP_200_OK)

        try:
            watch = self.watch_svc.create(
                user_id=str(request.user.id),
                name=d["name"],
                is_active=d["is_active"],
                feed_ids=d["feed_ids"],
                actions=actions,
            )
        except PolicyError as exc:
            return Response({"actions": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        except ConcurrentChainError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        # Anonymous telemetry (no-op unless opted in). Emitted from this API seam,
        # not the service, so the quickstart seed isn't counted (quickstart_completed
        # covers the install). No enabled() pre-gate like feed_created: the props are
        # already in memory (no query to skip) and capture() self-gates. Guarded so a
        # hiccup never fails the create.
        with telemetry_events.guard():
            telemetry_events.watch_created(
                action_kinds=[a.kind for a in actions],
                feed_count=len(d["feed_ids"]),
                surface=getattr(request, "surface", Surface.API.value),
            )
        return Response(
            watch_mutation(
                watch,
                feed_ids=self.watch_svc.feed_ids(watch),
                actions=self.watch_svc.initial_actions(watch),
                dry_run=False,
            ).model_dump(mode="json"),
            status=status.HTTP_201_CREATED,
        )

    def get(self, request):
        limit = parse_limit(request)
        after = request.query_params.get("after") or None
        watches = self.watch_svc.list(after=after, limit=limit)
        next_cursor = str(watches[-1].id) if len(watches) == limit else None
        items = [watch_wire(w, feed_ids=self.watch_svc.feed_ids(w)) for w in watches]
        return Response(WatchListResponse(items=items, next_cursor=next_cursor).model_dump(mode="json"))


class WatchDetailView(WatchScopedAPIView):
    """GET / PUT / DELETE /v1/watches/<id>, account-scoped."""

    def get(self, request, watch_id: str):
        return Response(
            watch_view(
                self.watch,
                feed_ids=self.watch_svc.feed_ids(self.watch),
                actions=self.watch_svc.initial_actions(self.watch),
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    def put(self, request, watch_id: str):
        serializer = WatchCreateSerializer(data=request.data, context={"account_id": request.account_id})
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        edit = {"name": d["name"], "is_active": d["is_active"], "feed_ids": d["feed_ids"], "actions": d["actions"]}
        try:
            if is_truthy(request.query_params.get("dry_run")):
                body = watch_mutation(self.watch, feed_ids=d["feed_ids"], actions=[], dry_run=True).model_dump(
                    mode="json"
                )
                body["actions"] = [_preview_action_wire(a, rank) for rank, a in enumerate(d["actions"])]
                return Response(body, status=status.HTTP_200_OK)
            updated = self.watch_svc.update(self.watch, **edit)
            return Response(
                watch_mutation(
                    updated,
                    feed_ids=self.watch_svc.feed_ids(updated),
                    actions=self.watch_svc.initial_actions(updated),
                    dry_run=False,
                ).model_dump(mode="json"),
                status=status.HTTP_200_OK,
            )
        except PolicyError as exc:
            return Response({"actions": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        except ConcurrentChainError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)

    def delete(self, request, watch_id: str):
        self.watch_svc.delete(self.watch)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WatchActionsView(WatchScopedAPIView):
    """Action chain sub-router on `/v1/watches/<id>/actions`.

    GET  list the watch's initial-path actions (the chain), rank order
    POST add one action (body = {config, rank?}); appends when rank omitted
    """

    def get(self, request, watch_id: str):
        return Response(
            {
                "items": [
                    watch_action_wire(a).model_dump(mode="json") for a in self.watch_svc.initial_actions(self.watch)
                ]
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, watch_id: str):
        body = request.data
        if not isinstance(body, dict):
            return Response(
                {"detail": "request body must be a JSON object with a `config`"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        config = body.get("config")
        if not isinstance(config, dict):
            return Response(
                {"config": ["this field is required and must be an object"]}, status=status.HTTP_400_BAD_REQUEST
            )
        rank = body.get("rank")
        if rank is not None and not isinstance(rank, int):
            return Response({"rank": ["must be an integer or omitted"]}, status=status.HTTP_400_BAD_REQUEST)
        kind_err = _validate_kind(body.get("kind"))
        if kind_err is not None:
            return kind_err
        # A committed watch always has an initial path (set at create), so
        # this is the chain's path id directly ; no lazy-create.
        try:
            created = self.action_svc.add(
                path_id=self.watch.initial_path_id,
                action=WatchActionInput(kind=str(body["kind"]), config=config),
                rank=rank,
            )
        except PydanticValidationError as exc:
            return Response({"config": pydantic_errors_to_drf(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PolicyError as exc:
            return Response({"config": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        except ConcurrentChainError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(watch_action_wire(created).model_dump(mode="json"), status=status.HTTP_201_CREATED)


class ActionDetailView(ActionScopedAPIView):
    """Per-action ops on `/v1/actions/<action_id>`, addressed by the action's
    own (globally unique) id — account-scoped, the watch/chain derived from
    the action rather than passed in the URL.

    GET    read this action's definition (kind + redacted config + summary)
    PUT    replace this action's config in place (same rank)
    DELETE remove the action and close the rank gap
    """

    def get(self, request, action_id: str):
        # `self.action` is the account-scoped row (404 via WatchActionNotFound).
        # Review path for `magpie watch action get`: the definition only, not
        # its runs/deliveries (those are the audit routes that hang off it).
        return Response(watch_action_wire(self.action).model_dump(mode="json"))

    def put(self, request, action_id: str):
        body = request.data
        if not isinstance(body, dict):
            return Response(
                {"detail": "request body must be a JSON object with a `config`"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        config = body.get("config")
        if not isinstance(config, dict):
            return Response(
                {"config": ["this field is required and must be an object"]}, status=status.HTTP_400_BAD_REQUEST
            )
        kind_err = _validate_kind(body.get("kind"))
        if kind_err is not None:
            return kind_err
        try:
            updated = self.action_svc.set_config(
                self.action, spec=WatchActionInput(kind=str(body["kind"]), config=config)
            )
        except PydanticValidationError as exc:
            return Response({"config": pydantic_errors_to_drf(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except PolicyError as exc:
            return Response({"config": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        return Response(watch_action_wire(updated).model_dump(mode="json"), status=status.HTTP_200_OK)

    def delete(self, request, action_id: str):
        try:
            self.action_svc.remove(self.action)
        except ConcurrentChainError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _preview_action_wire(action: WatchActionInput, rank: int) -> dict:
    """Render a not-yet-persisted action for a dry-run preview, from the
    already-validated input (config is the normalized dump)."""
    from .registry import parse_config

    config = parse_config(action.kind, action.config)
    return {
        "id": "",
        "kind": action.kind,
        "rank": rank,
        "config": config.redacted_dump(),
        "summary": config.summary().model_dump(mode="json"),
        "created_at": None,
    }
