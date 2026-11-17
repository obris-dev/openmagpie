"""`POST /v1/watches/<id>/connect-bower`: create an OpenBower custom signal for
the watch and add the `bower` action delivering to it (services.bower_connect).
`?dry_run=true` answers what it would create, without calling OpenBower."""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.response import Response

from common.api_params import wants_dry_run
from common.pydantic_errors import pydantic_errors_to_drf
from openmagpie_schema.watch import BowerConnectInput, BowerConnectResponse

from .api import WatchScopedAPIView
from .serializers import watch_action_wire
from .services.bower_connect import BowerConnectError, BowerConnectService


class WatchBowerConnectView(WatchScopedAPIView):
    def post(self, request, watch_id: str):
        try:
            body = BowerConnectInput.model_validate(request.data)
        except PydanticValidationError as exc:
            return Response(pydantic_errors_to_drf(exc), status=status.HTTP_400_BAD_REQUEST)
        service = BowerConnectService(account_id=request.account_id)
        try:
            if wants_dry_run(request):
                label, columns = service.preview(self.watch, body)
                preview = BowerConnectResponse(dry_run=True, signal_label=label, columns=columns)
                return Response(preview.model_dump(mode="json"), status=status.HTTP_200_OK)
            connection = service.connect(self.watch, body)
        except BowerConnectError as exc:
            return Response({"detail": str(exc)}, status=exc.status)
        wire = watch_action_wire(connection.action)
        if wire is None:
            raise RuntimeError(f"the bower action {connection.action.id} added just now does not render")
        answer = BowerConnectResponse(
            dry_run=False,
            signal_id=connection.signal_id,
            signal_label=connection.signal_label,
            columns=connection.columns,
            action=wire,
        )
        return Response(answer.model_dump(mode="json"), status=status.HTTP_201_CREATED)
