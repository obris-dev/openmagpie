"""`POST /v1/watches/<id>/connect-bower`: connect the watch to an OpenBower
custom signal, a new one or an existing one, by adding the `bower` action
(services.bower_connect). `?dry_run=true` answers what it would do, adding
nothing."""

from __future__ import annotations

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.response import Response

from common.api_params import wants_dry_run
from common.pydantic_errors import pydantic_errors_to_drf
from openmagpie_schema.watch import BowerConnectInput, BowerConnectResponse

from .api import WatchScopedAPIView
from .serializers import watch_action_wire
from .services.bower_connect import BowerConnectError, BowerConnectService

# The connect's two request shapes, told apart by which URL a body carries.
_INPUT: TypeAdapter[BowerConnectInput] = TypeAdapter(BowerConnectInput)


class WatchBowerConnectView(WatchScopedAPIView):
    def post(self, request, watch_id: str):
        try:
            body = _INPUT.validate_python(request.data)
        except PydanticValidationError as exc:
            return Response(pydantic_errors_to_drf(exc), status=status.HTTP_400_BAD_REQUEST)
        service = BowerConnectService(account_id=request.account_id)
        try:
            if wants_dry_run(request):
                plan = service.preview(self.watch, body)
                preview = BowerConnectResponse(
                    dry_run=True,
                    signal_id=plan.signal_id,
                    signal_label=plan.signal_label,
                    columns=plan.columns,
                    unmatched_fields=plan.unmatched_fields,
                    left_out_fields=plan.left_out_fields,
                )
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
            unmatched_fields=connection.unmatched_fields,
            left_out_fields=connection.left_out_fields,
            action=wire,
        )
        return Response(answer.model_dump(mode="json"), status=status.HTTP_201_CREATED)
